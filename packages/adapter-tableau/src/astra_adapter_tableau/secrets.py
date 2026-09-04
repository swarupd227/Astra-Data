"""Connection credentials, and the fact that this adapter never keeps one — S2.2.2.

    "Connection credentials are never stored; the adapter references a Key Vault secret by
    name." — S2.2.2

This is the criterion with teeth, because **Tableau workbooks contain credentials**. A `.twb`
is XML a person edited, and its `<connection>` elements routinely carry `username=`, and
sometimes `password=`. Anyone who has opened a real workbook has seen it. An adapter that
parsed connections naively would carry those into the Estate Graph, into the event stream that
S1.1.3 makes permanent and replayable, and into every context an agent is ever given.

So there are two halves here:

1. **Strip.** Attribute names that can carry a secret are removed at the point of parsing,
   before anything is built from them — not filtered later on the way out, because "later" is
   a place a future code path can skip.
2. **Reference.** What the platform needs is not the credential but a way to *name* one, so
   §18.1's Key Vault can hold it and E11's executor can ask for it. `connection_ref` on the
   Datasource node is that name: derived from the connection's identity, deterministic, and
   containing nothing secret.

The reference is derived rather than configured because a client with 400 workbooks has
perhaps a dozen distinct connections, and asking an operator to enumerate them before the
first harvest would make the harvest wait on a spreadsheet. Deriving means the platform can
*report* which secrets it needs — a list an operator can act on — rather than requiring the
list up front.
"""

from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)

#: Attributes a Tableau connection element may carry that can hold a secret, or help someone
#: reconstruct one. Removed before the connection is modelled.
#:
#: Deliberately a denylist of *names* rather than a heuristic on values: a value-based rule
#: ("looks like a password") both misses and over-matches, and a reviewer cannot tell what it
#: will do. A name that is not on this list and turns out to carry a secret is a bug to fix
#: by adding the name, which is a one-line, reviewable change.
SECRET_ATTRIBUTES = frozenset(
    {
        "password",
        "pwd",
        "passwd",
        "secret",
        "token",
        "access-token",
        "accesstoken",
        "refresh-token",
        "api-key",
        "apikey",
        "private-key",
        "privatekey",
        "sslcert",
        "ssl-cert",
        "keychain",
        "credential",
        "credentials",
        "connection-password",
        "oauth-token",
        "session-id",
        "sessionid",
        "auth-token",
        # A username is not a secret, but half of a credential pair is still client PII and
        # the platform has no use for it: ownership comes from the directory (S1.2.3), not
        # from a connection string.
        "username",
        "user",
        "uid",
        "one-time-sql",
    }
)

#: Where derived references live. A path rather than a flat name so a Key Vault can be
#: organised, and so a client can see at a glance which secrets belong to which site.
REFERENCE_PREFIX = "tableau"

_UNSAFE = re.compile(r"[^a-z0-9._-]+")


def strip_secrets(attributes: dict[str, str]) -> tuple[dict[str, str], tuple[str, ...]]:
    """Remove anything that could be a credential. Returns what is left, and what was removed.

    The *names* of what was removed are returned and recorded on the connection, because
    "this workbook had an embedded password" is a real finding for a migration programme —
    the client will have to rotate it, and the target model must not be built assuming the
    connection authenticates the way the old one did. Only the names; never the values.
    """
    kept: dict[str, str] = {}
    removed: list[str] = []
    for name, value in attributes.items():
        if name.lower() in SECRET_ATTRIBUTES:
            removed.append(name.lower())
            continue
        kept[name] = value
    if removed:
        logger.info(
            "stripped %d credential attribute(s) from a connection: %s",
            len(removed),
            ", ".join(sorted(removed)),
        )
    return kept, tuple(sorted(removed))


def secret_reference(*, site: str, connection_class: str, server: str, database: str = "") -> str:
    """A Key Vault secret *name* for a connection's credential (S2.2.2).

    Derived from the connection's identity so that two workbooks reaching the same warehouse
    produce the same reference and the client provisions one secret, not four hundred.

    The server host is hashed rather than spelled out. A Key Vault secret name is not
    especially secret, but an internal hostname is exactly the kind of thing that ends up in
    a screenshot in a status deck — and the reference only has to be *stable and unique*, not
    readable. The class and database stay legible so an operator can tell which of a dozen
    references is which.
    """
    fingerprint = hashlib.blake2b(
        f"{connection_class}|{server}|{database}".lower().encode(), digest_size=6
    ).hexdigest()
    parts = [REFERENCE_PREFIX, _slug(site) or "default", _slug(connection_class) or "unknown"]
    if database:
        parts.append(_slug(database))
    parts.append(fingerprint)
    return "/".join(parts)


def _slug(value: str) -> str:
    return _UNSAFE.sub("-", value.strip().lower()).strip("-")
