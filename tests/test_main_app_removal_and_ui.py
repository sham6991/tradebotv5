from pathlib import Path

from web_app import WebTradeBotApp
from websocket_owner_controller import ALLOWED_OWNERS, OWNER_INTRADAY, OWNER_MAIN_APP, OWNER_NONE


ROOT = Path(__file__).resolve().parents[1]


def test_removed_runtime_routes_are_not_registered():
    app = WebTradeBotApp()

    assert not hasattr(app, "options" + "_auto_routes")
    assert app.main_app_architecture_snapshot()["allowed_websocket_owners"] == [OWNER_NONE, OWNER_MAIN_APP, OWNER_INTRADAY]


def test_no_removed_page_or_route_reference_remains_in_runtime_files():
    runtime_files = [
        ROOT / "web_app.py",
        ROOT / "websocket_owner_controller.py",
        ROOT / "web_static" / "index.html",
        ROOT / "web_static" / "app.js",
    ]
    forbidden = ("options" + "_auto", "Options" + "Auto", "options" + "-auto", "/api/options" + "-auto")
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text


def test_websocket_owner_allowed_set_is_main_intraday_none():
    assert ALLOWED_OWNERS == {OWNER_NONE, OWNER_MAIN_APP, OWNER_INTRADAY}
