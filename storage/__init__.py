"""Storage execution adapters for the staged Cloudflare migration."""

from storage.errors import (
    StorageConstraintError,
    StorageError,
    StorageForeignKeyError,
    StorageUnavailableError,
    StorageUniqueError,
)
from storage.provider import create_storage, get_storage
from storage.result import StorageResult

__all__ = [
    "StorageConstraintError",
    "StorageError",
    "StorageForeignKeyError",
    "StorageResult",
    "StorageUnavailableError",
    "StorageUniqueError",
    "create_storage",
    "get_storage",
]
