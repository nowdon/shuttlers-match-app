"""Check a running local Worker or the deployed PoC using only the stdlib."""

import json
import sys
from urllib.request import Request, urlopen


def check(base_url):
    expected = {
        "health": {
            "status": "ok",
            "runtime": "cloudflare-python-worker",
            "framework": "flask",
        },
        "blueprint": {"status": "ok", "blueprint": "cloudflare_poc"},
    }
    for route in ("health", "blueprint", "template"):
        url = f"{base_url.rstrip('/')}/cloudflare-poc/{route}"
        request = Request(
            url, headers={"User-Agent": "shuttlers-match-flask-poc-smoke/0.1"}
        )
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            assert response.status == 200, (url, response.status)
            assert response.headers.get("Set-Cookie") is None
            if route in expected:
                assert response.headers.get_content_type() == "application/json"
                assert json.loads(body) == expected[route], (url, body)
            else:
                assert response.headers.get_content_type() == "text/html"
                assert "<h1>shuttlers-match-app</h1>" in body, body
                assert "<p>Cloudflare Flask PoC</p>" in body, body
                assert "{{" not in body, body
        print(f"PASS 200 {url}")


if __name__ == "__main__":
    check(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787")
