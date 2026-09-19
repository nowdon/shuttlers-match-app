"""Backend-neutral errors for the small history-dump mail boundary."""


class MailConfigurationError(RuntimeError):
    """Raised when the selected mail transport cannot be configured safely."""


class MailDeliveryError(RuntimeError):
    """Raised when a configured transport rejects a message.

    ``code`` is retained for operational logs and tests, while ``detail`` is
    deliberately not included in the public exception message. Provider error
    details must not leak through an application-level flash message.
    """

    def __init__(self, *, code=None, detail=None):
        self.code = code
        self.detail = detail
        suffix = f" (code={code})" if code else ""
        super().__init__(f"Mail delivery failed{suffix}.")
