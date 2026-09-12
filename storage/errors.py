"""Safe, backend-neutral storage exceptions."""


class StorageError(RuntimeError):
    """Base error whose string form is safe to show outside the storage layer."""

    category = "storage"
    default_message = "Storage operation failed."

    def __init__(self, message=None, *, detail=None):
        self.safe_message = message or self.default_message
        self._detail = detail
        super().__init__(self.safe_message)


class StorageConstraintError(StorageError):
    category = "constraint"
    default_message = "Stored data violates a constraint."


class StorageUniqueError(StorageConstraintError):
    category = "unique"
    default_message = "A record with the same unique value already exists."


class StorageForeignKeyError(StorageConstraintError):
    category = "foreign_key"
    default_message = "A referenced record does not exist or is still in use."


class StorageUnavailableError(StorageError):
    category = "unavailable"
    default_message = "The selected storage backend is unavailable."


def normalize_storage_error(error):
    """Map SQLite/D1 constraint messages without exposing their raw text."""
    raw_message = str(error)
    lowered = raw_message.lower()
    if "unique constraint" in lowered:
        return StorageUniqueError(detail=raw_message)
    if "foreign key constraint" in lowered:
        return StorageForeignKeyError(detail=raw_message)
    return StorageError(detail=raw_message)
