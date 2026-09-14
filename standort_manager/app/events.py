"""Ereignisprotokoll.

Kernpunkt für die Mehrsprachigkeit: Ein Eintrag speichert den Schlüssel und
die Parameter, nie den fertigen Satz. Stellt der Benutzer die Sprache um,
wechselt auch das alte Protokoll mit – und eine später ergänzte Sprache
erfasst rückwirkend alles, was schon aufgezeichnet wurde.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

from i18n import translator

log = logging.getLogger("standort.events")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
EVENT_FILE = DATA_DIR / "events.json"

MAX_EVENTS = 2000
# Beim Beenden wird nur ein Teil gesichert; das Protokoll ist Diagnose,
# kein Archiv.
PERSIST_EVENTS = 500

LEVELS = ("debug", "info", "warning", "error")


class EventLog:
    def __init__(self):
        self._lock = threading.Lock()
        self._events = deque(maxlen=MAX_EVENTS)
        self._seq = 0
        self.load()

    def load(self) -> None:
        if not EVENT_FILE.is_file():
            return
        try:
            data = json.loads(EVENT_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self._lock:
            for entry in data[-MAX_EVENTS:]:
                self._events.append(entry)
                self._seq = max(self._seq, entry.get("id", 0))

    def save(self) -> None:
        with self._lock:
            data = list(self._events)[-PERSIST_EVENTS:]
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            EVENT_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as err:
            log.warning("Protokoll nicht speicherbar: %s", err)

    def add(self, key: str, level: str = "info", device_id: str | None = None,
            **params):
        """Ereignis aufzeichnen.

        `key` ist ein Übersetzungsschlüssel wie 'event.device_arrived',
        `params` sind dessen Platzhalter.

        `device_id` ist bewusst vom Platzhalter `{device}` getrennt: der
        eine filtert die Anzeige, der andere steht im Satz. Hießen beide
        gleich, stünde im Protokoll die Geräte-ID statt des Namens.
        """
        if level not in LEVELS:
            level = "info"
        with self._lock:
            self._seq += 1
            entry = {
                "id": self._seq,
                "ts": time.time(),
                "level": level,
                "key": key,
                "params": {k: _plain(v) for k, v in params.items()},
            }
            if device_id:
                entry["device"] = device_id
            self._events.append(entry)
        # Ins Container-Protokoll in der Rückfallsprache, damit die
        # Add-on-Protokollansicht unabhängig von der Oberfläche lesbar ist.
        log.log(
            {"debug": logging.DEBUG, "info": logging.INFO,
             "warning": logging.WARNING, "error": logging.ERROR}[level],
            translator.t(key, "en", **params),
        )
        return entry

    def list(self, lang: str, level: str | None = None,
             device: str | None = None, limit: int = 200) -> list:
        """Protokoll übersetzt ausgeben."""
        wanted = LEVELS[LEVELS.index(level):] if level in LEVELS else LEVELS
        with self._lock:
            events = list(self._events)
        out = []
        for entry in reversed(events):
            if entry["level"] not in wanted:
                continue
            if device and entry.get("device") != device:
                continue
            out.append(
                {
                    **entry,
                    "text": translator.t(entry["key"], lang, **entry.get("params", {})),
                }
            )
            if len(out) >= limit:
                break
        return out

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
        self.save()


def _plain(value):
    """Nur JSON-fähige Werte ins Protokoll lassen."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


events = EventLog()
