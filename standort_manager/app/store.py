"""Einstellungen, Gerätezuordnung und Zugangsdaten unter /data.

Zugangsdaten werden mit Fernet verschlüsselt abgelegt. Das ist kein Schutz
gegen jemanden, der ohnehin Dateizugriff auf /data hat – der Schlüssel liegt
daneben. Es verhindert aber, dass das Passwort in Sicherungen, Protokollen
oder beim Durchblättern von Dateien im Klartext auftaucht.
"""

import json
import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("standort.store")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"
SECRET_FILE = DATA_DIR / "secret.key"
SESSION_DIR = DATA_DIR / "apple-session"

# Vorgaben. Alles, was hier steht, kann in der Oberfläche geändert werden;
# die Add-on-Optionen liefern nur die Startwerte beim allerersten Lauf.
DEFAULTS = {
    "sprache": "de",
    "einheiten": "metrisch",
    "zeitformat": "24h",
    "entity_praefix": "standort",
    "intervalle": {
        # Sekunden. Die Werte sind bewusst träge – Apple sperrt sonst.
        "zuhause": 900,
        "abwesend": 300,
        "unterwegs": 120,
        "zonennaehe": 60,
        "offline": 1200,
    },
    "genauigkeitsschwelle": 100,
    "geocode": {
        "anbieter": "nominatim",
        "sprache_folgt_oberflaeche": True,
    },
    "route": {
        "anbieter": "osrm",
        "heimzone": "zone.home",
    },
    "durchfahrt_sekunden": 30,
    "geraete": {},
    # Apple-ID -> {password}. Mehrere Konten sind möglich, etwa wenn die
    # Familie auf zwei Apple-IDs verteilt ist.
    "apple_accounts": {},
    # device_id -> Kurzform für Entitäts-IDs. Einmal vergeben, nie geändert.
    "entity_slugs": {},
    "zonen": {},
}


class Store:
    """Gemeinsamer Zustand, von mehreren Threads benutzt."""

    def __init__(self):
        self._lock = threading.RLock()
        self._data = {}
        self._fernet = None
        self.load()

    # ---------------------------------------------------------------- Datei

    def load(self) -> None:
        with self._lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            raw = {}
            if CONFIG_FILE.is_file():
                try:
                    raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                except (OSError, ValueError) as err:
                    log.error("config.json unlesbar, starte mit Vorgaben: %s", err)
            self._data = _merge(DEFAULTS, raw)
            self._migriere_konto()

    def _migriere_konto(self) -> None:
        """Das frühere Einzelkonto in die Kontenliste überführen.

        Ohne diesen Schritt wäre nach dem Update niemand mehr angemeldet -
        und der Benutzer müsste wieder einen Bestätigungscode eintippen,
        ohne zu wissen warum.
        """
        alt = self._data.pop("apple", None)
        if not alt or not alt.get("username"):
            return
        konten = self._data.setdefault("apple_accounts", {})
        konten.setdefault(alt["username"], {})
        if alt.get("password"):
            konten[alt["username"]]["password"] = alt["password"]
        log.info("Apple-Konto %s in die Kontenliste übernommen", alt["username"])
        self.save()

    def save(self) -> None:
        with self._lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Erst schreiben, dann umbenennen: ein Stromausfall mitten im
            # Schreiben hinterlaesst sonst eine halbe Datei.
            tmp.replace(CONFIG_FILE)

    # --------------------------------------------------------------- Zugriff

    def get(self, *path, default=None):
        with self._lock:
            node = self._data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    def set(self, *path_and_value) -> None:
        *path, value = path_and_value
        with self._lock:
            node = self._data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = value
        self.save()

    def all(self) -> dict:
        """Kopie ohne Geheimnisse – so geht der Zustand an die Oberfläche."""
        with self._lock:
            data = json.loads(json.dumps(self._data))
        data.pop("apple", None)
        return data

    # ------------------------------------------------------------ Geheimnis

    def _key(self) -> Fernet:
        if self._fernet:
            return self._fernet
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if SECRET_FILE.is_file():
            key = SECRET_FILE.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            SECRET_FILE.write_bytes(key)
            SECRET_FILE.chmod(0o600)
            log.info("Neuen Schlüssel für die Zugangsdaten erzeugt")
        self._fernet = Fernet(key)
        return self._fernet

    def set_credentials(self, username: str, password: str | None) -> None:
        """Zugangsdaten eines Kontos ablegen. password=None löscht nur das
        Passwort, nicht das Konto."""
        with self._lock:
            konten = self._data.setdefault("apple_accounts", {})
            eintrag = konten.setdefault(username, {})
            if password is None:
                eintrag.pop("password", None)
            else:
                eintrag["password"] = self._key().encrypt(password.encode()).decode()
        self.save()

    def credentials(self, username: str | None = None) -> tuple[str | None, str | None]:
        """Zugangsdaten eines Kontos.

        Ohne Angabe das erste Konto - so bleiben ältere Aufrufer heil, die
        noch von genau einem Konto ausgingen.
        """
        with self._lock:
            konten = self._data.get("apple_accounts", {})
            if username is None:
                username = next(iter(konten), None)
            if not username:
                return None, None
            token = (konten.get(username) or {}).get("password")
        if not token:
            return username, None
        try:
            return username, self._key().decrypt(token.encode()).decode()
        except InvalidToken:
            # Passiert, wenn secret.key weg ist, die config aber nicht.
            log.warning("Passwort von %s nicht entschlüsselbar – wird verworfen",
                        username)
            self.set_credentials(username, None)
            return username, None

    def accounts(self) -> list[str]:
        """Alle hinterlegten Apple-IDs, in der Reihenfolge des Anlegens."""
        with self._lock:
            return list(self._data.get("apple_accounts", {}))

    def remove_account(self, username: str) -> None:
        with self._lock:
            self._data.get("apple_accounts", {}).pop(username, None)
        self.save()

    def clear_credentials(self, username: str | None = None) -> None:
        """Ein Konto vergessen - ohne Angabe alle."""
        with self._lock:
            if username is None:
                self._data["apple_accounts"] = {}
            else:
                self._data.get("apple_accounts", {}).pop(username, None)
        self.save()

    # -------------------------------------------------------------- Geräte

    def device(self, device_id: str) -> dict:
        """Gespeicherte Einstellungen eines Geräts, mit Vorgaben aufgefüllt."""
        stored = self.get("geraete", device_id, default={}) or {}
        return {
            "modus": stored.get("modus", "inactive"),
            "name": stored.get("name"),
            "person": stored.get("person"),
            "companion_entity": stored.get("companion_entity"),
            "reference_zone": stored.get("reference_zone"),
            "timezone_offset": stored.get("timezone_offset", 0),
            "bild": stored.get("bild"),
        }

    def set_device(self, device_id: str, **felder) -> None:
        with self._lock:
            geraete = self._data.setdefault("geraete", {})
            eintrag = geraete.setdefault(device_id, {})
            for key, value in felder.items():
                if value is None:
                    eintrag.pop(key, None)
                else:
                    eintrag[key] = value
        self.save()


def _merge(base: dict, override: dict) -> dict:
    """Vorgaben mit gespeicherten Werten überlagern, verschachtelt.

    Wichtig für Updates: Eine neue Einstellung in DEFAULTS taucht dadurch
    auch in einer alten config.json auf, statt zu fehlen.
    """
    out = json.loads(json.dumps(base))
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


store = Store()
