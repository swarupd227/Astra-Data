"""Getting the workbook XML out of what Tableau sends — S2.2.1's second criterion.

    "Fetch retrieves .twb and .twbx (unpacking the XML from the archive)"

A ``.twb`` is the workbook XML. A ``.twbx`` is a zip containing that XML plus whatever the
workbook needs to be self-contained: images, custom shapes, and — the part that matters —
``.hyper`` extracts, which are the client's actual data.

**The extract is not taken.** §16 and S2.2.2 both say so, and the download already asks
Tableau not to include one. This module is the second line: it reads the XML entry and never
the data entries, so a ``.twbx`` that arrives with an extract anyway (a Tableau version that
ignores the flag, a proxy that served a cached copy) still does not put client data into the
platform's memory. What it *does* record is that an extract was there and what it was called,
because the Modeller needs to know where data comes from.

**Zip files from a client system are untrusted input** (§16.5). A zip can name an entry
``../../etc/passwd``, and can expand to a thousand times its compressed size. Nothing here
writes to disk, which removes the first; the second is bounded explicitly.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from typing import Any

from astra_adapter import AdapterError

logger = logging.getLogger(__name__)

#: A ``.twb`` this large is not a workbook. The largest real ones seen are a few tens of
#: megabytes of XML; the cap exists so a decompression bomb fails as a bounded read rather
#: than as an out-of-memory kill that takes the worker with it.
MAX_XML_BYTES = 256 * 1024 * 1024

#: Entries whose bytes are never read. Named rather than inferred from size, so that
#: "we did not copy the client's data" is a property of the code and not of a threshold.
DATA_SUFFIXES = (".hyper", ".tde", ".csv", ".xlsx", ".xls", ".json", ".parquet")


@dataclass(frozen=True, slots=True)
class WorkbookArchive:
    """The XML, and what else the archive was carrying."""

    xml: bytes
    packaged: bool
    """True for a ``.twbx``. Recorded because a packaged workbook and a live-connected one
    are different migration propositions and the estate should say which this is."""

    entry_name: str = ""
    extracts: tuple[str, ...] = ()
    """Names of extract files found. **Names only** — no bytes are read from them."""

    resources: tuple[str, ...] = ()
    total_entries: int = 0

    def as_properties(self) -> dict[str, Any]:
        return {
            "packaged": self.packaged,
            "archive_entries": self.total_entries,
            "extracts": list(self.extracts),
            "extract_count": len(self.extracts),
            "resources": len(self.resources),
        }


def is_zip(payload: bytes) -> bool:
    """A ``.twbx`` is a zip; a ``.twb`` is XML. Checked by magic number rather than by the
    filename Tableau's ``Content-Disposition`` suggested, because that header is advisory and
    the bytes are not."""
    return payload[:2] == b"PK"


def extract_workbook_xml(payload: bytes, *, name: str = "") -> WorkbookArchive:
    """Return the workbook XML, unpacking a ``.twbx`` if that is what arrived."""
    if not payload:
        raise AdapterError(
            f"the download for {name or 'the workbook'} was empty; Tableau returned no bytes",
            retryable=True,
        )

    if not is_zip(payload):
        _check_looks_like_workbook(payload, name)
        return WorkbookArchive(xml=payload, packaged=False, total_entries=1)

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise AdapterError(
            f"the download for {name or 'the workbook'} begins like a zip but cannot be "
            f"opened as one: {exc}. The file is corrupt, not merely unexpected.",
            retryable=False,
        ) from exc

    entries = archive.infolist()
    xml_entries = [item for item in entries if item.filename.lower().endswith(".twb")]
    if not xml_entries:
        raise AdapterError(
            f"the .twbx for {name or 'the workbook'} contains no .twb: "
            f"{', '.join(item.filename for item in entries[:8]) or '(empty archive)'}",
            retryable=False,
        )
    if len(xml_entries) > 1:
        # Never seen from Tableau, and if it happened the adapter would be choosing which
        # workbook the estate records. Refusing is the only answer that cannot be silently
        # wrong.
        raise AdapterError(
            f"the .twbx for {name or 'the workbook'} contains {len(xml_entries)} .twb files "
            f"({', '.join(item.filename for item in xml_entries)}); which one is the workbook "
            f"is not something this adapter should guess",
            retryable=False,
        )

    entry = xml_entries[0]
    if entry.file_size > MAX_XML_BYTES:
        raise AdapterError(
            f"{entry.filename} in {name or 'the workbook'} claims to be "
            f"{entry.file_size / 1e6:.0f} MB uncompressed, beyond the "
            f"{MAX_XML_BYTES / 1e6:.0f} MB this adapter will read",
            retryable=False,
        )

    with archive.open(entry) as handle:
        xml = handle.read(MAX_XML_BYTES + 1)
    if len(xml) > MAX_XML_BYTES:
        # The declared size can lie; the read is what is bounded.
        raise AdapterError(
            f"{entry.filename} in {name or 'the workbook'} expanded beyond "
            f"{MAX_XML_BYTES / 1e6:.0f} MB and was not read further",
            retryable=False,
        )

    _check_looks_like_workbook(xml, name)

    extracts = tuple(
        item.filename for item in entries if item.filename.lower().endswith(DATA_SUFFIXES)
    )
    resources = tuple(
        item.filename
        for item in entries
        if item is not entry and item.filename not in extracts and not item.is_dir()
    )
    if extracts:
        logger.info(
            "%s carries %d extract file(s); names recorded, bytes not read",
            name or "the workbook",
            len(extracts),
        )

    return WorkbookArchive(
        xml=xml,
        packaged=True,
        entry_name=entry.filename,
        extracts=extracts,
        resources=resources,
        total_entries=len(entries),
    )


def _check_looks_like_workbook(payload: bytes, name: str) -> None:
    """Refuse something that is not a workbook, with what it looks like instead.

    The usual cause is an HTML login page served by a proxy in front of Tableau, delivered
    with a 200. Parsing that as a workbook produces a parse failure three layers away from
    the cause; saying "this is HTML" here ends the search immediately.
    """
    head = payload[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<workbook"):
        return
    looks_like = "HTML" if head[:1] == b"<" else "not XML at all"
    raise AdapterError(
        f"what came back for {name or 'the workbook'} is {looks_like}, not a Tableau "
        f"workbook. A login page or an error page served by something in front of Tableau "
        f"is the usual cause. First bytes: {head[:80]!r}",
        retryable=False,
    )
