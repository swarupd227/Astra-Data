"""graph-svc — the Astra Data Estate Graph service.

Spec §4.1 (the Estate Graph), §5.2 (component inventory).
"""

from .ontology import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION", "__version__"]

__version__ = "0.1.0"
