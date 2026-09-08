"""Request ONLY PoC diagnostics/health; never a production application route."""
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def check(base):
    def get(path):
        request = Request(base.rstrip("/") + "/cloudflare-import-poc/" + path,
                          headers={"User-Agent": "shuttlers-match-import-poc-smoke/0.1"})
        try:
            return urlopen(request, timeout=30)
        except HTTPError as error:
            return error

    with get("diagnostics") as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "application/json"
        assert response.headers.get("Set-Cookie") is None
        report = json.load(response)
    assert len(report["results"]) == 11
    assert not any(report["blocked_attempts"].values())
    assert report["initial_app_import"]["status"] == ("ok" if report["app_imported"] else "error")
    timezone = report["initial_zoneinfo"]
    assert timezone["status"] == "ok"
    assert timezone["key"] == "Asia/Tokyo"
    assert timezone["fixed_datetime"] == "2026-01-01T12:00:00+09:00"
    assert timezone["utc_offset"] == "+09:00"
    assert timezone["utc_offset_seconds"] == 32400
    assert timezone["tzdata_version"] == "2025.3"
    if report["app_imported"]:
        assert report["status"] == "ok"
        assert report["app"] == {
            "blueprints": ["admin", "api", "history", "line", "match", "participant"],
            "secret_key_is_none": True, "runtime_initialized": False,
        }
    with get("health") as response:
        assert response.status == (200 if report["app_imported"] else 404)
        if report["app_imported"]:
            assert json.load(response)["blueprints"] == report["app"]["blueprints"]
    return report


if __name__ == "__main__":
    print(json.dumps(check(sys.argv[1]), indent=2))
