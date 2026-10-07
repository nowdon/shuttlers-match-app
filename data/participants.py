"""Named Participant reads and writes across the storage boundary."""

from dataclasses import dataclass

from models import Participant


PARTICIPANT_COLUMNS = "id, name, gender, level, weight, games_played, active, card"


@dataclass(frozen=True)
class ParticipantRecord:
    id: int
    name: str
    gender: str
    level: str
    weight: float
    games_played: int
    active: bool
    card: str


def _record(row):
    if row is None:
        return None
    return ParticipantRecord(
        id=int(row["id"]),
        name=row["name"],
        gender=row["gender"],
        level=row["level"],
        weight=float(row["weight"]),
        games_played=int(row["games_played"] or 0),
        active=bool(row["active"]),
        card=row["card"],
    )


def _adapter(storage):
    if storage is not None:
        return storage
    from flask import has_request_context
    from storage.provider import create_storage, get_storage
    return get_storage() if has_request_context() else create_storage()


def get_participant_by_card(card, *, storage=None):
    row = _adapter(storage).first(
        f"SELECT {PARTICIPANT_COLUMNS} FROM participants WHERE card = ?", card
    )
    return _record(row)


def get_all_participants(*, storage=None):
    rows = _adapter(storage).all(
        f"SELECT {PARTICIPANT_COLUMNS} FROM participants ORDER BY id"
    )
    return [_record(row) for row in rows]


def get_participants_ordered_by_card(*, storage=None):
    rows = _adapter(storage).all(
        f"SELECT {PARTICIPANT_COLUMNS} FROM participants ORDER BY card"
    )
    return [_record(row) for row in rows]


def get_participants_by_ids(participant_ids, *, storage=None):
    unique_ids = list(dict.fromkeys(participant_ids))
    if not unique_ids:
        return []
    placeholders = ", ".join("?" for _ in unique_ids)
    rows = _adapter(storage).all(
        f"SELECT {PARTICIPANT_COLUMNS} FROM participants "
        f"WHERE id IN ({placeholders}) ORDER BY id",
        *unique_ids,
    )
    return [_record(row) for row in rows]


def get_active_participants(*, storage=None):
    rows = _adapter(storage).all(
        f"SELECT {PARTICIPANT_COLUMNS} FROM participants "
        "WHERE active = 1 ORDER BY id"
    )
    return [_record(row) for row in rows]


def get_api_participants(*, storage=None):
    rows = _adapter(storage).all(
        "SELECT id, name, gender, level, active FROM participants ORDER BY id"
    )
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "gender": row["gender"],
            "level": row["level"],
            "active": bool(row["active"]),
        }
        for row in rows
    ]


def create_participant(name, gender, level, weight, card, *, storage=None):
    result = _adapter(storage).run(
        "INSERT INTO participants "
        "(name, gender, level, weight, games_played, active, card) "
        "VALUES (?, ?, ?, ?, 0, 1, ?) RETURNING " + PARTICIPANT_COLUMNS,
        name,
        gender,
        level,
        weight,
        card,
    )
    return _record(result.rows[0])


def update_participant_by_card(
    card, *, name, gender, level, active, storage=None
):
    result = _adapter(storage).run(
        "UPDATE participants SET name = ?, gender = ?, level = ?, active = ? "
        "WHERE card = ? RETURNING " + PARTICIPANT_COLUMNS,
        name,
        gender,
        level,
        1 if active else 0,
        card,
    )
    return _record(result.rows[0]) if result.rows else None


def create_participants_bulk(participants, *, storage=None):
    rows = list(participants)
    if not rows:
        return []
    statements = [
        (
            "INSERT INTO participants "
            "(name, gender, level, weight, games_played, active, card) "
            "VALUES (?, ?, ?, ?, 0, 1, ?)",
            (row["name"], row["gender"], row["level"], row["weight"], row["card"]),
        )
        for row in rows
    ]
    return _adapter(storage).batch(statements)


def get_participants_by_ids_for_orm_mutation(participant_ids):
    """Transitional helper for match confirm/revert dirty tracking only."""
    ids = list(dict.fromkeys(participant_ids))
    if not ids:
        return []
    return Participant.query.filter(Participant.id.in_(ids)).all()


def get_all_participants_for_orm():
    """Transitional read for unmigrated match/history code using ORM objects."""
    return Participant.query.all()


def get_active_participants_for_orm():
    """Transitional read for match generation, which mutates candidate objects."""
    return Participant.query.filter_by(active=True).all()


def get_participants_by_ids_for_orm(participant_ids):
    """Transitional read for remaining ORM-based match rendering flows."""
    ids = list(dict.fromkeys(participant_ids))
    if not ids:
        return []
    return Participant.query.filter(Participant.id.in_(ids)).all()
