"""Zugriff auf Home Assistant über den Supervisor-Proxy.

Wird für zwei Dinge gebraucht: die Zonen (damit Zonen nicht doppelt gepflegt
werden müssen) und die Tracker der Companion-App als zweite Standortquelle.
"""

import logging
import os
import time

import requests

log = logging.getLogger("standort.ha")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_API = "http://supervisor/core/api"
TIMEOUT = 10

# Zonen ändern sich selten; jede Abfrage kostet aber einen Roundtrip.
ZONE_TTL = 300

# Zonen, die eine andere Ortungsintegration selbst erzeugt und wieder
# einzieht. Sie stehen dort, wo zufällig ein Gerät länger stand, wandern
# mit und verschwinden von selbst - als Zone gewertet ergäbe das ständige
# Ankunfts- und Abfahrtsmeldungen an Orten ohne jede Bedeutung.
FREMDE_ZONEN_PRAEFIXE = (
    "zone.ic3_stationary_",   # iCloud3
    "zone.ic3_statzon",       # iCloud3, ältere Namensform
)

_zone_cache: tuple[float, list] = (0.0, [])


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }


def available() -> bool:
    return bool(SUPERVISOR_TOKEN)


def states(entity_id: str | None = None):
    """Zustände abrufen. Ohne entity_id alle."""
    if not available():
        return None
    url = f"{CORE_API}/states" + (f"/{entity_id}" if entity_id else "")
    try:
        resp = requests.get(url, headers=_headers(), timeout=TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as err:
        log.warning("Home Assistant nicht erreichbar: %s", err)
        return None


def zones(force: bool = False) -> list:
    """Alle Zonen aus Home Assistant.

    Die Heimzone kommt zusätzlich aus der Kernkonfiguration, weil sie in
    manchen Installationen keine eigene zone-Entität hat.
    """
    global _zone_cache
    age, cached = _zone_cache
    if not force and cached and time.time() - age < ZONE_TTL:
        return cached

    alle = states() or []
    out = []
    for entry in alle:
        entity_id = entry.get("entity_id", "")
        if not entity_id.startswith("zone."):
            continue
        if entity_id.startswith(FREMDE_ZONEN_PRAEFIXE):
            continue
        attrs = entry.get("attributes", {})
        if attrs.get("latitude") is None:
            continue
        out.append(
            {
                "entity_id": entry["entity_id"],
                "name": attrs.get("friendly_name", entry["entity_id"]),
                "latitude": attrs["latitude"],
                "longitude": attrs["longitude"],
                "radius": attrs.get("radius", 100),
                "passive": bool(attrs.get("passive")),
                "icon": attrs.get("icon"),
            }
        )

    if not any(z["entity_id"] == "zone.home" for z in out):
        cfg = _core_config()
        if cfg and cfg.get("latitude") is not None:
            out.insert(
                0,
                {
                    "entity_id": "zone.home",
                    "name": cfg.get("location_name", "Home"),
                    "latitude": cfg["latitude"],
                    "longitude": cfg["longitude"],
                    "radius": 100,
                    "passive": False,
                    "icon": "mdi:home",
                },
            )

    if out:
        _zone_cache = (time.time(), out)
    return out


def _core_config() -> dict | None:
    if not available():
        return None
    try:
        resp = requests.get(f"{CORE_API}/config", headers=_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def device_trackers() -> list:
    """Alle device_tracker-Entitäten – zur Auswahl der Companion-Quelle."""
    alle = states() or []
    out = []
    for entry in alle:
        eid = entry.get("entity_id", "")
        if not eid.startswith("device_tracker."):
            continue
        attrs = entry.get("attributes", {})
        # Eigene Entitäten nicht als Quelle anbieten – das gäbe eine Schleife.
        if attrs.get("attribution") == "Standort-Manager":
            continue
        out.append(
            {
                "entity_id": eid,
                "name": attrs.get("friendly_name", eid),
                "state": entry.get("state"),
                "source_type": attrs.get("source_type"),
                "has_gps": attrs.get("latitude") is not None,
            }
        )
    return out


def tracker_location(entity_id: str) -> dict | None:
    """Standort eines Companion-App-Trackers."""
    entry = states(entity_id)
    if not entry:
        return None
    attrs = entry.get("attributes", {})
    if attrs.get("latitude") is None:
        return None
    changed = entry.get("last_updated") or entry.get("last_changed")
    return {
        "latitude": attrs["latitude"],
        "longitude": attrs["longitude"],
        "accuracy": attrs.get("gps_accuracy"),
        "timestamp": _parse_ts(changed),
        "battery": attrs.get("battery_level"),
        "state": entry.get("state"),
        "is_old": False,
        "position_type": "GPS",
        "finished": True,
    }


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
