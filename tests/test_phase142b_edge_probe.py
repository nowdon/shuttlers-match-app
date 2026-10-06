"""Offline checks for the disposable one-hop edge probe."""

import json

from tests import run_phase142b_edge_probe as probe
from tests import run_phase142b_propagation as propagation
from tests.run_phase142b_edge_probe import _base_config, _tail_summary


def test_edge_probe_starts_without_worker_hops_or_bindings():
    config = _base_config("shuttlers-phase14-disposable-test", "synthetic-token")

    assert config["workers_dev"] is True
    assert "services" not in config
    assert "assets" not in config
    assert "d1_databases" not in config
    assert "r2_buckets" not in config


def test_tail_summary_keeps_diagnostics_without_request_headers(tmp_path):
    event = {
        "scriptName": "shuttlers-phase14-disposable-test",
        "outcome": "exception",
        "cpuTime": 53,
        "wallTime": 120,
        "event": {"request": {
            "url": "https://example.workers.dev/viewer?private=value",
            "headers": {"cf-ray": "synthetic-ray", "authorization": "private-token"},
        }},
        "exceptions": [{"name": "RuntimeError", "message": "synthetic failure"}],
        "logs": [{"level": "error", "message": ["synthetic stack"]}],
    }
    path = tmp_path / "tail.jsonl"
    path.write_text("Connecting...\n" + json.dumps(event, indent=2) + "\n")

    summary = _tail_summary(path)

    assert summary == [{
        "outcome": "exception", "cpu_ms": 53, "wall_ms": 120,
        "path": "/viewer", "cf_ray": "synthetic-ray",
        "exceptions": [{"name": "RuntimeError", "message": "synthetic failure"}],
        "logs": [{"message": ["synthetic stack"], "level": "error"}],
    }]
    assert "private-token" not in json.dumps(summary)
    assert "private=value" not in json.dumps(summary)


def test_readiness_requires_ten_consecutive_expected_markers(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "_run", lambda *_args, **_kwargs: json.dumps({
        "versions": [{"version_id": "synthetic-version", "percentage": 100}]}))
    markers = iter(["expected", "prior"] + ["expected"] * 10)
    monkeypatch.setattr(probe, "_request", lambda *_args, **_kwargs: {
        "status": 200, "version_marker": next(markers)})
    monkeypatch.setattr(probe.time, "sleep", lambda _seconds: None)

    result = probe._readiness("wrangler", "disposable", tmp_path, "https://example.invalid",
                              "synthetic-token", "expected", "synthetic-version")

    assert result["passed"] is True
    assert len(result["probes"]) == 12


def test_deployment_status_report_excludes_account_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(propagation, "_run", lambda *_args, **_kwargs: json.dumps({
        "author_email": "private@example.invalid",
        "versions": [{"version_id": "synthetic-version", "percentage": 100}],
    }))

    assert propagation._status("wrangler", "disposable", tmp_path) == [
        {"version_id": "synthetic-version", "percentage": 100}]
