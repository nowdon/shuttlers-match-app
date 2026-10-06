"""The Worker asset bundle must contain exactly the named 54-card set."""

import struct
import zlib

import pytest

from scripts.card_asset_inventory import EXPECTED_NAMES, audit_cards
from scripts import prepare_worker_bundle


def _chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def _png(rgb):
    width, height = 409, 600
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) +
            _chunk(b"IDAT", zlib.compress(scanline * height)) +
            _chunk(b"IEND", b""))


def test_card_inventory_requires_exact_names_and_unique_png_content(tmp_path):
    assert len(EXPECTED_NAMES) == 54
    cards = tmp_path / "cards"
    cards.mkdir()
    for number, name in enumerate(sorted(EXPECTED_NAMES)):
        (cards / name).write_bytes(_png((number, 0, 0)))

    report = audit_cards(cards)

    assert report["ok"] is True
    assert report["valid"] == 54
    assert not (report["missing"] or report["unexpected"] or report["invalid"] or
                report["duplicate_content"])

    (cards / "hA.png").unlink()
    (cards / "extra.png").write_bytes(_png((255, 0, 0)))
    broken = audit_cards(cards)
    assert broken["ok"] is False
    assert broken["missing"] == ["hA.png"]
    assert broken["unexpected"] == ["extra.png"]


def test_card_inventory_rejects_corrupt_png_chunks(tmp_path):
    cards = tmp_path / "cards"
    cards.mkdir()
    data = bytearray(_png((1, 2, 3)))
    data[50] ^= 1  # Alter compressed pixels without fixing the IDAT CRC.
    (cards / "hA.png").write_bytes(data)
    assert audit_cards(cards)["invalid"] == ["hA.png"]


def test_worker_stage_fails_before_copy_when_clean_checkout_lacks_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_worker_bundle, "ROOT", tmp_path)
    config = tmp_path / "private.jsonc"
    config.write_text("{}")
    destination = tmp_path / "stage"

    with pytest.raises(ValueError, match="Worker card assets are incomplete"):
        prepare_worker_bundle.prepare(destination, config)

    assert not destination.exists()


def test_worker_stage_copies_only_validated_operator_cards(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare_worker_bundle, "ROOT", tmp_path)
    config = tmp_path / "private.jsonc"
    config.write_text("{}")
    for name in prepare_worker_bundle.SOURCE_FILES:
        (tmp_path / name).write_text("# synthetic test input\n")
    for name in prepare_worker_bundle.SOURCE_DIRECTORIES:
        (tmp_path / name).mkdir()
    (tmp_path / "templates").mkdir()
    source = tmp_path / "static" / "cards"
    source.mkdir(parents=True)
    (tmp_path / "static" / "participants_template.csv").write_text("name,card\n")
    for number, name in enumerate(sorted(EXPECTED_NAMES)):
        (source / name).write_bytes(_png((number, 0, 0)))

    stage = prepare_worker_bundle.prepare(tmp_path / "stage", config)
    staged = stage / "static" / "cards"
    assert (stage / "pylock.toml").read_text() == (tmp_path / "pylock.toml").read_text()
    assert not (stage / "python_modules").exists()
    assert audit_cards(staged)["ok"] is True
    assert {path.name for path in staged.iterdir()} == EXPECTED_NAMES
    for name in EXPECTED_NAMES:
        assert (staged / name).read_bytes() == (source / name).read_bytes()

    damaged = bytearray((source / "hA.png").read_bytes())
    damaged[50] ^= 1
    (source / "hA.png").write_bytes(damaged)
    with pytest.raises(ValueError, match="Worker card assets are incomplete"):
        prepare_worker_bundle.prepare(tmp_path / "rejected-stage", config)
    assert not (tmp_path / "rejected-stage").exists()
