from __future__ import annotations

from datetime import date, datetime
from typing import Any


class OptionsGreeksRiskEngine:
    """Lightweight option risk proxy.

    Greeks are not fabricated; this produces theta/expiry/IV-risk proxies unless
    reliable IV/Greek fields are explicitly supplied by the data source.
    """

    def evaluate(self, candidate: dict[str, Any], settings: dict[str, Any] | None = None, today: date | None = None) -> dict[str, Any]:
        settings = dict(settings or {})
        blockers = []
        warnings = []
        today = today or date.today()
        expiry = _parse_date(candidate.get("expiry"))
        days_to_expiry = (expiry - today).days if expiry else None
        moneyness = str(candidate.get("moneyness") or "").upper()
        spread_pct = float(candidate.get("spread_pct") or 0)
        if days_to_expiry is not None and days_to_expiry <= 0:
            warnings.append("Expiry-day theta/gamma risk is high.")
            if int(settings.get("expiry_day_max_lots") or 1) <= 0:
                blockers.append("Expiry-day trading is disabled.")
        if moneyness == "OTM" and days_to_expiry is not None and days_to_expiry <= 1:
            blockers.append("Near-expiry OTM theta risk is too high.")
        if spread_pct > float(settings.get("max_spread_pct") or 0.6):
            blockers.append("Spread makes break-even speed unattractive.")
        iv = candidate.get("iv")
        greeks_available = iv not in ("", None)
        score = 100.0 - len(blockers) * 40.0 - len(warnings) * 12.0
        return {
            "allowed": not blockers,
            "theta_risk_score": max(0.0, min(100.0, score)),
            "days_to_expiry": days_to_expiry,
            "greeks_available": greeks_available,
            "blockers": blockers,
            "warnings": warnings,
        }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None

