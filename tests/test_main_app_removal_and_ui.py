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


def test_websocket_owner_card_shows_operational_fields():
    html = (ROOT / "web_static" / "index.html").read_text(encoding="utf-8")

    assert "Zerodha Connection & Websocket Owner" in html
    assert 'id="websocket-owner-card"' in html
    assert "Activate Main App Owner" in html
    assert "does not start ticks by itself" in html
    for field_id in (
        "ws-owner-preferred",
        "ws-owner-active",
        "ws-owner-mode",
        "ws-owner-ticker",
        "ws-owner-token-count",
        "ws-owner-last-tick",
        "ws-owner-health",
        "ws-owner-blockers",
        "ws-owner-main",
        "ws-owner-intraday",
        "ws-owner-release",
        "ws-owner-stop",
    ):
        assert field_id in html


def test_main_app_settings_surface_is_simplified_and_limit_only():
    script = (ROOT / "web_static" / "app.js").read_text(encoding="utf-8")
    labels = (ROOT / "settings_service.py").read_text(encoding="utf-8")

    for visible_label in (
        "Essentials",
        "Risk",
        "Advanced Expert",
        "underlying_id",
        "risk_mode",
        "entry_logic",
        "allow_price_only_direction_when_futures_unavailable",
    ):
        assert visible_label in script

    for hidden_key in (
        '"order_product"',
        '"live_option_market_entry_as_limit_enabled"',
        '"live_option_market_entry_limit_buffer_points"',
    ):
        assert hidden_key in script
    assert "hiddenUiSettings" in script
    assert "Market Entry Score" not in labels
    assert "Live Option Market Entry As Limit" in labels


def test_underlying_selection_drives_index_ui_and_market_context_card():
    html = (ROOT / "web_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "web_static" / "app.js").read_text(encoding="utf-8")

    assert "Market Context" in html
    assert "market-context-list" in html
    assert 'data-action="fetch-index"' in script
    assert "/api/live/fetch-index" in script
    assert "selectedUnderlyingMeta" in script
    assert "SENSEX" in script
    assert "Fetch NIFTY" in html
    assert "Fetch SENSEX" not in html
