import importlib


EXPECTED_BLUEPRINT_ROUTES = {
    ("/", frozenset({"GET"}), "root_redirect"),
    ("/register", frozenset({"GET", "POST"}), "register"),
    ("/qrcode/<user_type>", frozenset({"GET"}), "qrcode_image"),
    ("/thanks", frozenset({"GET"}), "thanks"),
    ("/participant/<card>", frozenset({"GET", "POST"}), "participant_view"),
    ("/viewer", frozenset({"GET"}), "viewer_index"),
    ("/line/webhook", frozenset({"POST"}), "line_webhook"),
    (
        "/notifications/line/start/<card>",
        frozenset({"GET"}),
        "start_line_notification",
    ),
    (
        "/notifications/line/unsubscribe/<card>",
        frozenset({"POST"}),
        "unsubscribe_line_notification",
    ),
    ("/upload", frozenset({"GET", "POST"}), "upload_csv"),
    ("/download_template", frozenset({"GET"}), "download_template"),
    ("/admin/settings", frozenset({"GET", "POST"}), "admin_settings"),
    ("/admin/reset_db", frozenset({"POST"}), "reset_db"),
    ("/admin", frozenset({"GET"}), "admin_index"),
    ("/match", frozenset({"GET", "POST"}), "match_form"),
    ("/match/edit", frozenset({"GET"}), "edit_matches"),
    ("/match/optimize_pairs", frozenset({"POST"}), "optimize_pairs"),
    ("/match/swap", frozenset({"POST"}), "swap_players"),
    ("/match/confirm", frozenset({"POST"}), "confirm_match"),
    (
        "/match/revert_to_draft",
        frozenset({"POST"}),
        "revert_match_to_draft",
    ),
    ("/update_court_count", frozenset({"POST"}), "update_court_count"),
    ("/match/result", frozenset({"GET"}), "match_result"),
    ("/match/draft", frozenset({"GET"}), "match_draft"),
    ("/match_result", frozenset({"GET"}), "legacy_match_result"),
    ("/reset_match", frozenset({"POST"}), "reset_match"),
    (
        "/admin/match_history/round/<int:round_id>/score",
        frozenset({"POST"}),
        "update_match_history_round_score",
    ),
    (
        "/match/result/round/<int:round_id>/score",
        frozenset({"POST"}),
        "update_match_result_round_score",
    ),
    (
        "/admin/match_history/<int:match_history_id>/score",
        frozenset({"POST"}),
        "update_match_history_score",
    ),
    (
        "/match/result/<int:match_history_id>/score",
        frozenset({"POST"}),
        "update_match_result_score",
    ),
    (
        "/admin/match_history/dump",
        frozenset({"POST"}),
        "dump_match_history",
    ),
    (
        "/admin/match_history/dump_and_clear",
        frozenset({"POST"}),
        "dump_and_clear_match_history",
    ),
    ("/admin/match_history", frozenset({"GET"}), "admin_match_history"),
    (
        "/admin/match_history_archives",
        frozenset({"GET"}),
        "admin_match_history_archives",
    ),
    (
        "/admin/match_history_archives/<path:filename>",
        frozenset({"GET"}),
        "admin_match_history_archive_detail",
    ),
}


def test_blueprint_routes_preserve_url_methods_and_endpoint_names():
    app = importlib.import_module("app").app
    actual_routes = {
        (
            rule.rule,
            frozenset(rule.methods - {"HEAD", "OPTIONS"}),
            rule.endpoint,
        )
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static" and not rule.endpoint.startswith("api.")
    }

    assert actual_routes == EXPECTED_BLUEPRINT_ROUTES


def test_expected_blueprints_are_registered():
    app = importlib.import_module("app").app
    assert {"participant", "line", "admin", "match", "history"} <= set(
        app.blueprints
    )
