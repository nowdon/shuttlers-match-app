"""Fixed template classification and synthetic render contexts for the Jinja PoC."""
from datetime import datetime, timezone
from types import SimpleNamespace


TEMPLATES = {
    "button-styles": {
        "name": "_button_styles.html",
        "classification": "A",
        "render": True,
        "reason": "No context variables or includes.",
    },
    "flash-messages": {
        "name": "_flash_messages.html",
        "classification": "C",
        "render": False,
        "reason": "Reads Flask flash/session state; SECRET_KEY is intentionally unset.",
    },
    "admin-settings": {
        "name": "admin_settings.html",
        "classification": "C",
        "render": False,
        "reason": "Uses config values and the flash/session partial.",
    },
    "index": {
        "name": "index.html",
        "classification": "C",
        "render": False,
        "reason": "Uses participant, match/config state, and the flash/session partial.",
    },
    "line-link-token": {
        "name": "line_link_token.html",
        "classification": "A",
        "render": True,
        "reason": "A small synthetic participant/token context is sufficient.",
    },
    "match-edit": {
        "name": "match_edit.html",
        "classification": "C",
        "render": False,
        "reason": "Uses draft/match data, score helpers, and the flash/session partial.",
    },
    "match-form": {
        "name": "match_form.html",
        "classification": "C",
        "render": False,
        "reason": "Includes the flash/session partial.",
    },
    "match-history": {
        "name": "match_history.html",
        "classification": "C",
        "render": False,
        "reason": "Uses DB-backed history/config data and the flash/session partial.",
    },
    "match-history-archives": {
        "name": "match_history_archives.html",
        "classification": "C",
        "render": False,
        "reason": "Uses history-dump data and the flash/session partial.",
    },
    "match-result": {
        "name": "match_result.html",
        "classification": "C",
        "render": False,
        "reason": "Uses DB/match/config state and the flash/session partial.",
    },
    "participant-edit": {
        "name": "participant_edit.html",
        "classification": "A",
        "render": True,
        "reason": "A small synthetic participant context is sufficient.",
    },
    "register": {
        "name": "register.html",
        "classification": "C",
        "render": False,
        "reason": "Uses Flask request plus the flash/session partial.",
    },
    "thanks": {
        "name": "thanks.html",
        "classification": "C",
        "render": False,
        "reason": "Uses config/participant/LINE state and the flash/session partial.",
    },
    "upload-csv": {
        "name": "upload_csv.html",
        "classification": "A",
        "render": True,
        "reason": "Only a mode value is needed; exercises include and url_for.",
    },
}

RENDER_SLUGS = (
    "upload-csv",
    "button-styles",
    "line-link-token",
    "participant-edit",
)


def render_context(slug):
    """Return a fresh, deliberately small context for an allowlisted template."""
    if slug == "button-styles":
        return {}
    if slug == "upload-csv":
        return {"mode": "admin"}
    if slug == "line-link-token":
        return {
            "line_bot_friend_url": "",
            "mode": "viewer",
            "participant": SimpleNamespace(name="PoC participant"),
            "token": SimpleNamespace(
                token="POC123",
                expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            ),
        }
    if slug == "participant-edit":
        return {
            "card": "A1",
            "mode": "viewer",
            "participant": SimpleNamespace(
                active=True,
                card="A1",
                gender="male",
                level="intermediate",
                name="PoC participant",
            ),
        }
    raise KeyError(f"Template is not render-allowlisted: {slug}")
