"""Koordinaten in eine lesbare Adresse übersetzen.

Standard ist Nominatim (OpenStreetMap). Dessen Nutzungsregeln verlangen
einen aussagekräftigen User-Agent und höchstens eine Anfrage pro Sekunde –
beides hält dieses Modul ein. Zusätzlich wird grob gerastert
zwischengespeichert: steht ein Gerät still, fragt die App nicht alle zwei
Minuten dieselbe Adresse erneut ab.
"""

import logging
import threading
import time

import requests

log = logging.getLogger("standort.geocode")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "standort-manager/0.1 (Home Assistant Add-on; privater Gebrauch)"

MIN_INTERVAL = 1.1
CACHE_TTL = 3600
# Rund 11 m – feiner braucht eine Straßenadresse nicht aufgelöst zu werden.
CACHE_PRECISION = 4

_lock = threading.Lock()
_last_call = 0.0
_cache: dict[tuple, tuple[float, dict]] = {}


def reverse(lat: float, lon: float, lang: str = "de") -> dict | None:
    """Adresse zu Koordinaten. None, wenn nicht ermittelbar."""
    key = (round(lat, CACHE_PRECISION), round(lon, CACHE_PRECISION), lang)
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

    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "zoom": 18,
                "addressdetails": 1,
                "accept-language": lang,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as err:
        log.warning("Nominatim-Abfrage fehlgeschlagen: %s", err)
        return None

    result = _shape(data)
    if result:
        with _lock:
            _cache[key] = (now, result)
            _trim()
    return result


def _shape(data: dict) -> dict | None:
    addr = data.get("address") or {}
    if not addr:
        return None
    strasse = addr.get("road") or addr.get("pedestrian") or addr.get("footway")
    nummer = addr.get("house_number")
    ort = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("suburb")
    )
    zeile = ", ".join(
        teil
        for teil in (
            " ".join(x for x in (strasse, nummer) if x) or None,
            ort,
        )
        if teil
    )
    return {
        "display": zeile or data.get("display_name", ""),
        "full": data.get("display_name", ""),
        "street": strasse,
        "house_number": nummer,
        "postcode": addr.get("postcode"),
        "city": ort,
        "district": addr.get("suburb") or addr.get("city_district"),
        "state": addr.get("state"),
        "country": addr.get("country"),
        "country_code": (addr.get("country_code") or "").upper() or None,
    }


def _trim(limit: int = 500) -> None:
    """Cache klein halten – aufgerufen unter gehaltenem Lock."""
    if len(_cache) <= limit:
        return
    for key in sorted(_cache, key=lambda k: _cache[k][0])[: len(_cache) - limit]:
        _cache.pop(key, None)
