"""Lightweight scheduler placeholder for future pre-market automation.

Version 1 keeps fetching user-triggered from the dashboard/API to avoid
surprising network calls or any coupling with execution workflows.
"""


def scheduler_status() -> dict:
    return {"enabled": False, "message": "Manual fetch mode in Version 1."}
