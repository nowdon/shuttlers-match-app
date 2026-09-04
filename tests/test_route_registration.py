import importlib


EXPECTED_BLUEPRINT_ROUTES = {
    ("/", frozenset({"GET"}), "participant.root_redirect"),
    ("/register", frozenset({"GET", "POST"}), "participant.register"),
    ("/qrcode/<user_type>", frozenset({"GET"}), "participant.qrcode_image"),
    ("/thanks", frozenset({"GET"}), "participant.thanks"),
    ("/participant/<card>", frozenset({"GET", "POST"}), "participant.participant_view"),
    ("/viewer", frozenset({"GET"}), "participant.viewer_index"),
    ("/line/webhook", frozenset({"POST"}), "line.line_webhook"),
    (
        "/notifications/line/start/<card>",
        frozenset({"GET"}),
        "line.start_line_notification",
    ),
    (
        "/notifications/line/unsubscribe/<card>",
        frozenset({"POST"}),
        "line.unsubscribe_line_notification",
    ),
    ("/upload", frozenset({"GET", "POST"}), "admin.upload_csv"),
    ("/download_template", frozenset({"GET"}), "admin.download_template"),
    ("/admin/settings", frozenset({"GET", "POST"}), "admin.admin_settings"),
    ("/admin/reset_db", frozenset({"POST"}), "admin.reset_db"),
    ("/admin", frozenset({"GET"}), "admin.admin_index"),
    ("/match", frozenset({"GET", "POST"}), "match.match_form"),
    ("/match/edit", frozenset({"GET"}), "match.edit_matches"),
    ("/match/optimize_pairs", frozenset({"POST"}), "match.optimize_pairs"),
    ("/match/swap", frozenset({"POST"}), "match.swap_players"),
    ("/match/confirm", frozenset({"POST"}), "match.confirm_match"),
    (
        "/match/revert_to_draft",
        frozenset({"POST"}),
        "match.revert_match_to_draft",
    ),
    ("/update_court_count", frozenset({"POST"}), "match.update_court_count"),
    ("/match/result", frozenset({"GET"}), "match.match_result"),
    ("/match/draft", frozenset({"GET"}), "match.match_draft"),
    ("/match_result", frozenset({"GET"}), "match.legacy_match_result"),
    ("/reset_match", frozenset({"POST"}), "match.reset_match"),
    (
        "/admin/match_history/round/<int:round_id>/score",
        frozenset({"POST"}),
        "history.update_match_history_round_score",
    ),
    (
        "/match/result/round/<int:round_id>/score",
        frozenset({"POST"}),
        "history.update_match_result_round_score",
    ),
    (
        "/admin/match_history/<int:match_history_id>/score",
        frozenset({"POST"}),
        "history.update_match_history_score",
    ),
    (
        "/match/result/<int:match_history_id>/score",
        frozenset({"POST"}),
        "history.update_match_result_score",
    ),
    (
        "/admin/match_history/dump",
        frozenset({"POST"}),
        "history.dump_match_history",
    ),
    (
        "/admin/match_history/dump_and_clear",
        frozenset({"POST"}),
        "history.dump_and_clear_match_history",
    ),
    ("/admin/match_history", frozenset({"GET"}), "history.admin_match_history"),
    (
        "/admin/match_history_archives",
        frozenset({"GET"}),
        "history.admin_match_history_archives",
    ),
    (
        "/admin/match_history_archives/<path:filename>",
        frozenset({"GET"}),
        "history.admin_match_history_archive_detail",
    ),
}


def test_blueprint_routes_register_expected_urls_methods_and_endpoints():
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
