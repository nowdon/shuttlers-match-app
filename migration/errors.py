"""Errors raised by the Phase 10 tooling."""


class MigrationError(RuntimeError):
    """A source, plan, or safety precondition is not acceptable."""


class ValidationError(MigrationError):
    """A source or target validation check failed."""
