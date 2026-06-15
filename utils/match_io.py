from utils.draft_state import get_active_draft


def is_draft_active():
    return get_active_draft() is not None
