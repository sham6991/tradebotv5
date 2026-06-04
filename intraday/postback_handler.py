from __future__ import annotations


class PostbackHandler:
    def __init__(self, database):
        self.database = database

    def handle(self, payload: dict) -> dict:
        return {"accepted": False, "message": "Postbacks are not enabled for the local intraday terminal.", "payload": payload}
