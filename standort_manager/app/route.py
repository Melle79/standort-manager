"""Fahrtdauer zur Heimzone.

Standard ist der öffentliche OSRM-Demoserver. Der ist für den privaten
Hausgebrauch gedacht und ausdrücklich nicht für Dauerlast – deshalb wird
nur gerechnet, wenn ein Gerät sich tatsächlich bewegt, und das Ergebnis
für die Dauer der Fahrt zwischengespeichert.
"""

import logging
import threading
import time

import requests

log = logging.getLogger("standort.route")

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
TIMEOUT = 15
CACHE_TTL = 120
MIN_INTERVAL = 2.0

_lock = threading.Lock()
_last_call = 0.0
_cache: dict[tuple, tuple[float, dict]] = {}


def travel_time(from_lat: float, from_lon: float,
                to_lat: float, to_lon: float) -> dict | None:
    """Fahrzeit und Strecke. None, wenn keine Route bestimmbar."""
    key = (round(from_lat, 3), round(from_lon, 3), round(to_lat, 4), round(to_lon, 4))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]

    global _last_call
    with _lock:
        wait = MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()

    url = f"{OSRM_URL}/{from_lon},{from_lat};{to_lon},{to_lat}"
    try:
        resp = requests.get(
            url, params={"overview": "false", "alternatives": "false"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as err:
        log.warning("OSRM-Abfrage fehlgeschlagen: %s", err)
        return None

    routen = data.get("routes") or []
    if not routen:
        return None

    route = routen[0]
    result = {
        "duration_seconds": round(route.get("duration", 0)),
        "distance_meters": round(route.get("distance", 0)),
        "arrival_timestamp": time.time() + route.get("duration", 0),
    }
    with _lock:
        _cache[key] = (now, result)
        if len(_cache) > 300:
            for old in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(old, None)
    return result
