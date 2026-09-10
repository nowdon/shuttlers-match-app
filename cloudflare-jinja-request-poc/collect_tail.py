"""Sanitize Wrangler tail JSON and retain only request correlation evidence."""
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit


def collect(raw_path):
    text = Path(raw_path).read_text()
    decoder = json.JSONDecoder()
    index = text.find("{")
    records = []
    while index >= 0:
        try:
            event, length = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        index = text.find("{", index + length)
        request = event.get("event", {}).get("request", {})
        url = urlsplit(request.get("url", ""))
        probe = parse_qs(url.query).get("probe", [None])[0]
        if not probe or not probe.startswith("workers-dev-"):
            continue
        exceptions = []
        for error in event.get("exceptions", []):
            stack = error.get("stack", "")
            top = next(
                (
                    line.strip()
                    for line in stack.splitlines()
                    if line.strip().startswith(("at ", "File "))
                ),
                None,
            )
            message = str(error.get("message", ""))
            frames = [
                {"module": Path(module).name, "line": int(line), "function": function}
                for module, line, function in re.findall(
                    r'File "([^"]+)", line (\d+), in ([^\n]+)', message
                )
            ]
            terminal = next(
                (
                    line
                    for line in reversed(message.splitlines())
                    if line.startswith(("introspection.CpuLimitExceeded:", "SystemError:"))
                ),
                None,
            )
            exceptions.append(
                {
                    "type": error.get("name"),
                    "python_frames": frames,
                    "python_terminal": terminal,
                    "top_frame": top[:240] if top else None,
                    "message": message if error.get("name") in ("ErrnoError", "Error") else None,
                }
            )
        records.append(
            {
                "probe": probe,
                "path": url.path,
                "ray_id": request.get("headers", {}).get("cf-ray"),
                "outcome": event.get("outcome"),
                "cpu_ms": event.get("cpuTime"),
                "wall_ms": event.get("wallTime"),
                "status": event.get("event", {}).get("response", {}).get("status"),
                "version_id": event.get("scriptVersion", {}).get("id"),
                "exceptions": exceptions,
            }
        )
    return records


if __name__ == "__main__":
    rows = collect(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(rows, indent=2) + "\n")
    print(f"Sanitized events: {len(rows)}")
