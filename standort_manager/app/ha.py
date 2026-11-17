"""Zugriff auf Home Assistant über den Supervisor-Proxy.

Wird für zwei Dinge gebraucht: die Zonen (damit Zonen nicht doppelt gepflegt
werden müssen) und die Tracker der Companion-App als zweite Standortquelle.
"""

import json
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


def _companion_entitaeten() -> set[str] | None:
    """Entitäten der Companion-App (Integration mobile_app).

    Die REST-API kennt keine Entitätsregistrierung; die Template-API kann
    aber nach Integration fragen. None heißt: nicht ermittelbar.
    """
    try:
        resp = requests.post(
            f"{CORE_API}/template", headers=_headers(), timeout=TIMEOUT,
            json={"template": "{{ integration_entities('mobile_app') | tojson }}"},
        )
        resp.raise_for_status()
        return set(json.loads(resp.text))
    except (requests.RequestException, ValueError) as err:
        log.warning("Companion-Entitäten nicht ermittelbar: %s", err)
        return None


def device_trackers() -> list:
    """Tracker der Companion-App – zur Auswahl der Companion-Quelle.

    Früher standen hier alle device_tracker: iBeacons, Router-Tracker,
    Steckdosen. Nur die App liefert aber GPS-Standorte, die etwas taugen.
    """
    alle = states() or []
    companion = _companion_entitaeten()
    out = []
    for entry in alle:
        eid = entry.get("entity_id", "")
        if not eid.startswith("device_tracker."):
            continue
        attrs = entry.get("attributes", {})
        if companion is not None:
            if eid not in companion:
                continue
        elif attrs.get("source_type") != "gps":
            # Notbehelf, wenn HA die Integration nicht verrät.
            continue
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


def melde(titel: str, text: str, kennung: str = "standort_manager") -> bool:
    """Eine dauerhafte Benachrichtigung in Home Assistant anlegen.

    Für die Fälle, in denen das Add-on nicht weiterkommt und jemand etwas
    tun muss. Ohne sie merkt man erst Stunden später, dass nichts mehr
    aktualisiert wird.
    """
    if not available():
        return False
    try:
        resp = requests.post(
            f"{CORE_API}/services/persistent_notification/create",
            headers=_headers(),
            json={"title": titel, "message": text, "notification_id": kennung},
            timeout=TIMEOUT,
        )
        _aufs_handy(titel, text)
        _reparatur(titel, text, kennung)
        return resp.ok
    except requests.RequestException as err:
        log.warning("Benachrichtigung nicht zustellbar: %s", err)
        return False


def _reparatur(titel: str, text: str, kennung: str) -> None:
    """Zusätzlich eine Reparaturmeldung, falls Spook installiert ist.

    Ein Add-on kann selbst keine Reparatur anlegen, nur Integrationen.
    Spook stellt dafür die Aktion repairs.create bereit. Ohne Spook
    antwortet HA mit einem Fehler - das ist kein Problem, dann bleibt es
    bei Glocke und Handy.
    """
    try:
        requests.post(
            f"{CORE_API}/services/repairs/create", headers=_headers(),
            json={"issue_id": kennung, "title": titel, "description": text,
                  "severity": "error", "persistent": True},
            timeout=TIMEOUT,
        )
    except requests.RequestException as err:
        log.debug("Reparaturmeldung nicht möglich: %s", err)


def _aufs_handy(titel: str, text: str) -> None:
    """Zusätzlich an einen notify-Dienst, falls in den Einstellungen gewählt.

    Die Glocke in Home Assistant sieht nur, wer HA gerade offen hat. Ein
    Stillstand der Ortung fiel so erst Tage später auf.
    """
    from store import store
    dienst = (store.get("benachrichtigung", default="") or "").strip()
    if not dienst.startswith("notify."):
        return
    try:
        requests.post(
            f"{CORE_API}/services/notify/{dienst.split('.', 1)[1]}",
            headers=_headers(), json={"title": titel, "message": text},
            timeout=TIMEOUT,
        )
    except requests.RequestException as err:
        log.warning("Handy-Benachrichtigung nicht zustellbar: %s", err)


def notify_dienste() -> list[str]:
    """Alle notify-Dienste, zur Auswahl in den Einstellungen."""
    if not available():
        return []
    try:
        resp = requests.get(f"{CORE_API}/services", headers=_headers(),
                            timeout=TIMEOUT)
        resp.raise_for_status()
        for domain in resp.json():
            if domain.get("domain") == "notify":
                return sorted(f"notify.{d}" for d in domain.get("services", {})
                              if d not in ("send_message", "persistent_notification"))
    except (requests.RequestException, ValueError) as err:
        log.warning("notify-Dienste nicht abrufbar: %s", err)
    return []


def meldung_zuruecknehmen(kennung: str = "standort_manager") -> None:
    """Die Benachrichtigung wieder entfernen, sobald das Problem weg ist."""
    if not available():
        return
    try:
        requests.post(
            f"{CORE_API}/services/persistent_notification/dismiss",
            headers=_headers(), json={"notification_id": kennung},
            timeout=TIMEOUT,
        )
        requests.post(
            f"{CORE_API}/services/repairs/remove",
            headers=_headers(), json={"issue_id": kennung},
            timeout=TIMEOUT,
        )
    except requests.RequestException:
        pass
