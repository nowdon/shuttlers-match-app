"""Phase 10 migration and rehearsal tooling.

The package intentionally contains a small, application-aware migration path
instead of a general ETL framework.  All commands operate on a cloned SQLite
snapshot or disposable local Cloudflare resources; they never select a remote
Wrangler target.
"""

from .errors import MigrationError, ValidationError

__all__ = ["MigrationError", "ValidationError"]
