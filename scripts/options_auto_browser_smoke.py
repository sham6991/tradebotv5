from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "http://127.0.0.1:8007/options-auto"


def _http_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError):
        return False


def _wait_ready(url: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _http_ready(url):
            return True
        time.sleep(0.5)
    return False


def _start_server_if_needed(url: str) -> subprocess.Popen[str] | None:
    if _http_ready(url):
        return None
    return subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _run_smoke(url: str) -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: python -m pip install -r requirements-dev.txt "
            "and then: python -m playwright install chromium"
        ) from exc

    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(url, wait_until="networkidle", timeout=30000)

        checks = {
            "title": "Options Auto" in page.title(),
            "shell": page.locator(".oa-shell").count() == 1,
            "news_panel": page.locator("#oa-news-event-panel").count() == 1,
            "real_approval_card": page.locator("#oa-real-approval-card").count() == 1,
        }

        page.get_by_role("button", name="Real Trading").click()
        checks["real_tab_visible"] = page.locator("#oa-tab-real").is_visible()
        checks["real_approve_visible"] = page.locator("#oa-real-approve-entry").is_visible()

        page.get_by_role("button", name="Settings").click()
        checks["settings_tab_visible"] = page.locator("#oa-tab-settings").is_visible()
        checks["dry_run_toggle"] = page.locator("#oa-dry-run-real").count() == 1
        checks["real_orders_toggle"] = page.locator("#oa-real-orders-enabled").count() == 1
        checks["real_auto_toggle"] = page.locator("#oa-real-auto-entry").count() == 1
        checks["ask_permission_toggle"] = page.locator("#oa-ask-settings").count() == 1

        dry_run_checked = page.locator("#oa-dry-run-real").is_checked()
        browser.close()

    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise AssertionError(f"Options Auto browser smoke failed checks: {', '.join(failed)}")
    if console_errors:
        raise AssertionError("Options Auto browser console errors: " + " | ".join(console_errors[:5]))
    return {"checks": checks, "dry_run_checked": dry_run_checked, "console_errors": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a headless browser smoke test for Options Auto.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Options Auto URL. Default: {DEFAULT_URL}")
    parser.add_argument("--startup-timeout", type=float, default=12.0)
    args = parser.parse_args()

    server = _start_server_if_needed(args.url)
    try:
        if not _wait_ready(args.url, args.startup_timeout):
            raise RuntimeError(f"Options Auto did not become ready at {args.url}")
        result = _run_smoke(args.url)
        print(
            "OPTIONS_AUTO_BROWSER_SMOKE_OK "
            f"checks={sum(bool(v) for v in result['checks'].values())} "
            f"dry_run_checked={result['dry_run_checked']} console_errors=0"
        )
        return 0
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
