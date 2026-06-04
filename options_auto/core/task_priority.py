from __future__ import annotations

P0_CRITICAL_PROTECTION = 0
P1_EXIT_AND_OCO = 1
P2_ENTRY = 2
P3_MONITORING = 3
P4_SLOW = 4

PRIORITY_NAMES = {
    P0_CRITICAL_PROTECTION: "P0_CRITICAL_PROTECTION",
    P1_EXIT_AND_OCO: "P1_EXIT_AND_OCO",
    P2_ENTRY: "P2_ENTRY",
    P3_MONITORING: "P3_MONITORING",
    P4_SLOW: "P4_SLOW",
}

FAST_LANE_DISALLOWED_TASKS = {
    "news_fetch",
    "fii_dii_parse",
    "full_option_chain_scan",
    "historical_fetch",
    "excel_write",
    "report_generation",
    "ui_full_redraw",
    "full_indicator_recompute",
    "backtest",
    "large_json_serialization",
}


def priority_name(priority: int) -> str:
    return PRIORITY_NAMES.get(int(priority), f"P{priority}")


def slow_lane_allowed_in_fast_validation(task_name: str) -> bool:
    return str(task_name or "").strip().lower() not in FAST_LANE_DISALLOWED_TASKS
