"""Small immutable records shared by the SMTP and Cloudflare transports."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MailAttachment:
    filename: str
    content: bytes
    content_type: str

    def __post_init__(self):
        object.__setattr__(self, "content", bytes(self.content))


@dataclass(frozen=True)
class MailMessage:
    recipient: str
    sender_email: str
    sender_name: str | None
    subject: str
    body: str
    attachment: MailAttachment | None = None
