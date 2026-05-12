import hashlib
import json
from datetime import date, datetime


SETTINGS_SCHEMA_VERSION = 1
_VOLATILE_KEYS = {
    "session_id",
    "settings_hash",
    "settings_version",
    "settings_schema_version",
}
_SECRET_MARKERS = ("secret", "token", "password", "credential")


def build_settings_profile(settings):
    normalized = normalize_settings(settings)
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    short_hash = digest[:16]
    return {
        "settings_schema_version": SETTINGS_SCHEMA_VERSION,
        "settings_hash": short_hash,
        "settings_version": f"settings-v{SETTINGS_SCHEMA_VERSION}-{short_hash}",
    }


def apply_settings_profile(settings):
    profile = build_settings_profile(settings)
    if settings is not None:
        settings.update(profile)
    return profile


def normalize_settings(settings):
    if not isinstance(settings, dict):
        return {}
    return {
        str(key): _normalize_value(value)
        for key, value in sorted(settings.items(), key=lambda item: str(item[0]))
        if _include_key(key)
    }


def profile_from_settings(settings):
    if not isinstance(settings, dict):
        return build_settings_profile({})
    existing = {
        "settings_schema_version": settings.get("settings_schema_version"),
        "settings_hash": settings.get("settings_hash"),
        "settings_version": settings.get("settings_version"),
    }
    if all(value not in ("", None) for value in existing.values()):
        return existing
    return build_settings_profile(settings)


def add_profile_to_payload(payload, settings):
    if isinstance(payload, dict):
        data = dict(payload)
    elif payload in ("", None):
        data = {}
    else:
        data = {"value": payload}
    for key, value in profile_from_settings(settings).items():
        data.setdefault(key, value)
    return data


def _include_key(key):
    text = str(key or "").lower()
    if text in _VOLATILE_KEYS:
        return False
    if text.startswith("_"):
        return False
    return not any(marker in text for marker in _SECRET_MARKERS)


def _normalize_value(value):
    if isinstance(value, dict):
        return {
            str(key): _normalize_value(item)
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
            if _include_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 10)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)
