"""Audit the exact card-image set without reading private application data."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CARD_DIMENSIONS = (409, 600)
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("h", "d", "c", "s")
EXPECTED_NAMES = frozenset(
    [f"{suit}{rank}.png" for suit in SUITS for rank in RANKS]
    + ["joker_red.png", "joker_black.png"]
)


def _scanline_lengths(width, height, depth, color, interlace):
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    passes = ((0, 0, 1, 1),) if interlace == 0 else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
    )
    for x, y, dx, dy in passes:
        columns = max(0, (width - x + dx - 1) // dx)
        rows = max(0, (height - y + dy - 1) // dy)
        if columns:
            yield from ((columns * channels * depth + 7) // 8 for _ in range(rows))


def valid_png(data):
    """Reject truncated or corrupted PNG chunks and image data without Pillow."""
    if not data.startswith(PNG_SIGNATURE):
        return False
    offset = len(PNG_SIGNATURE)
    seen_ihdr = seen_idat = seen_iend = False
    compressed = bytearray()
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            return False
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            return False
        if not seen_ihdr:
            if kind != b"IHDR" or length != 13:
                return False
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload)
            allowed_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16),
                              3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if ((width, height) != CARD_DIMENSIONS
                    or depth not in allowed_depths.get(color, ()) or compression != 0
                    or filtering != 0 or interlace not in (0, 1)):
                return False
            seen_ihdr = True
        elif kind == b"IDAT":
            seen_idat = True
            compressed.extend(payload)
        elif kind == b"IEND":
            seen_iend = length == 0 and end == len(data)
            break
        offset = end
    if not (seen_ihdr and seen_idat and seen_iend):
        return False
    try:
        decoder = zlib.decompressobj()
        # A 409x600 card cannot need more than 4 MiB of scanline data.
        decoded = decoder.decompress(bytes(compressed), 4 * 1024 * 1024 + 1)
        if not decoded or len(decoded) > 4 * 1024 * 1024 or not decoder.eof:
            return False
        if decoder.unused_data or decoder.unconsumed_tail:
            return False
        position = 0
        for row_bytes in _scanline_lengths(width, height, depth, color, interlace):
            if position + row_bytes + 1 > len(decoded) or decoded[position] > 4:
                return False
            position += row_bytes + 1
        return position == len(decoded)
    except zlib.error:
        return False


def audit_cards(directory):
    """Return a deterministic manifest and fail conditions for one asset directory."""
    directory = Path(directory)
    actual = {path.name: path for path in directory.iterdir()} if directory.is_dir() else {}
    missing = sorted(EXPECTED_NAMES - actual.keys())
    unexpected = sorted(actual.keys() - EXPECTED_NAMES)
    invalid = []
    files = []
    hashes = {}
    for name in sorted(EXPECTED_NAMES & actual.keys()):
        path = actual[name]
        if not path.is_file() or path.is_symlink():
            invalid.append(name)
            continue
        data = path.read_bytes()
        if not valid_png(data):
            invalid.append(name)
            continue
        dimensions = CARD_DIMENSIONS
        digest = hashlib.sha256(data).hexdigest()
        hashes.setdefault(digest, []).append(name)
        files.append({"path": f"static/cards/{name}", "format": "PNG",
                      "width": dimensions[0], "height": dimensions[1],
                      "bytes": len(data), "sha256": digest})
    duplicates = [names for names in hashes.values() if len(names) > 1]
    return {"expected": 54, "valid": len(files), "total_bytes": sum(x["bytes"] for x in files),
            "missing": missing, "unexpected": unexpected, "invalid": invalid,
            "duplicate_content": duplicates, "files": files,
            "ok": not (missing or unexpected or invalid or duplicates)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cards-dir", type=Path, default=Path("static/cards"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit_cards(args.cards_dir)
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
