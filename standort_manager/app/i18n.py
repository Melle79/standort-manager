"""Übersetzungskern für Backend und Oberfläche.

Grundregel des Projekts: Im Code steht nie ein fertiger Satz, sondern immer
nur ein Schlüssel plus Parameter. Ereignisse werden ebenfalls als Schlüssel
gespeichert und erst beim Anzeigen übersetzt – dadurch wechselt auch das
bereits aufgezeichnete Protokoll die Sprache, wenn der Benutzer umstellt.

Kataloge liegen als JSON unter /translations. Eine neue Sprache braucht
nichts weiter als eine weitere Datei; sie wird beim Start automatisch
gefunden.
"""

import json
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("standort.i18n")

TRANSLATIONS_DIR = Path(os.environ.get("TRANSLATIONS_DIR", "/translations"))

# Sprache, auf die zurückgefallen wird, wenn ein Schlüssel in der gewählten
# Sprache fehlt. Englisch, weil es der kleinste gemeinsame Nenner ist.
FALLBACK_LANG = "en"

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class Catalog:
    """Ein geladener Sprachkatalog."""

    def __init__(self, lang: str, data: dict):
        self.lang = lang
        self.meta = data.get("meta", {})
        self._flat = _flatten(data.get("strings", {}))

    @property
    def name(self) -> str:
        """Name der Sprache in der Sprache selbst, z.B. 'Deutsch'."""
        return self.meta.get("name", self.lang)

    def has(self, key: str) -> bool:
        return key in self._flat

    def get(self, key: str) -> str | None:
        return self._flat.get(key)

    def keys(self) -> set:
        return set(self._flat)


class Translator:
    """Lädt alle Kataloge und übersetzt Schlüssel."""

    def __init__(self, directory: Path = TRANSLATIONS_DIR):
        self.directory = Path(directory)
        self.catalogs: dict[str, Catalog] = {}
        self.reload()

    def reload(self) -> None:
        self.catalogs = {}
        if not self.directory.is_dir():
            log.error("Übersetzungsverzeichnis fehlt: %s", self.directory)
            return
        for path in sorted(self.directory.glob("*.json")):
            lang = path.stem
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as err:
                log.error("Katalog %s unlesbar: %s", path.name, err)
                continue
            self.catalogs[lang] = Catalog(lang, data)
        log.info("Sprachen geladen: %s", ", ".join(sorted(self.catalogs)) or "keine")

    @property
    def languages(self) -> list[dict]:
        """Liste für die Sprachauswahl in der Oberfläche."""
        out = []
        for lang in sorted(self.catalogs):
            cat = self.catalogs[lang]
            out.append(
                {
                    "code": lang,
                    "name": cat.name,
                    "complete": self.completeness(lang),
                }
            )
        return out

    def completeness(self, lang: str) -> int:
        """Wie viel Prozent der Schlüssel eine Sprache abdeckt.

        Maßstab ist die Sprache mit den meisten Schlüsseln, nicht die
        Rückfallsprache – sonst sieht eine unvollständige Rückfallsprache
        andere fälschlich als übervollständig aus.
        """
        if lang not in self.catalogs:
            return 0
        reference = max((c.keys() for c in self.catalogs.values()), key=len, default=set())
        if not reference:
            return 100
        have = self.catalogs[lang].keys() & reference
        return round(len(have) * 100 / len(reference))

    def resolve(self, lang: str | None) -> str:
        """Gewünschte Sprache auf eine tatsächlich vorhandene abbilden."""
        if lang and lang in self.catalogs:
            return lang
        if lang:
            # 'de-DE' → 'de'
            base = lang.split("-")[0].lower()
            if base in self.catalogs:
                return base
        if FALLBACK_LANG in self.catalogs:
            return FALLBACK_LANG
        return next(iter(self.catalogs), FALLBACK_LANG)

    def t(self, key: str, lang: str | None = None, **params) -> str:
        """Schlüssel übersetzen.

        Reihenfolge: gewählte Sprache → Rückfallsprache → der Schlüssel selbst.
        Der Schlüssel als letzte Stufe ist Absicht: eine fehlende Übersetzung
        soll auffallen, aber nie eine leere Zeile oder einen Absturz erzeugen.
        """
        text = None
        chain = [lang, FALLBACK_LANG] if lang else [FALLBACK_LANG]
        for candidate in chain:
            if not candidate:
                continue
            cat = self.catalogs.get(candidate) or self.catalogs.get(
                self.resolve(candidate)
            )
            if cat and cat.has(key):
                text = cat.get(key)
                break
        if text is None:
            log.debug("Übersetzung fehlt: %s (%s)", key, lang)
            return key
        # Ein Parameter darf selbst ein Schlüssel sein, etwa
        # reason='error.rate_limited'. Ohne diese Auflösung stünde der
        # nackte Schlüssel mitten im übersetzten Satz.
        aufgeloest = {
            name: self.t(wert, lang) if self._is_key(wert, lang) else wert
            for name, wert in params.items()
        }
        return _fill(text, aufgeloest)

    def _is_key(self, wert, lang: str | None) -> bool:
        """Sieht der Wert aus wie ein Schlüssel und gibt es ihn auch?"""
        if not isinstance(wert, str) or "." not in wert or " " in wert:
            return False
        for candidate in (lang, FALLBACK_LANG):
            cat = self.catalogs.get(self.resolve(candidate)) if candidate else None
            if cat and cat.has(wert):
                return True
        return False

    def catalog_for_ui(self, lang: str) -> dict:
        """Flacher Katalog für die Oberfläche.

        Enthält die Rückfallsprache als Unterbau, damit die Oberfläche bei
        einer halbfertigen Übersetzung nicht löchrig wird.
        """
        merged = {}
        fallback = self.catalogs.get(FALLBACK_LANG)
        if fallback:
            merged.update(fallback._flat)
        chosen = self.catalogs.get(self.resolve(lang))
        if chosen:
            merged.update(chosen._flat)
        return merged


def _flatten(data: dict, prefix: str = "") -> dict:
    """Verschachteltes JSON zu Punkt-Schlüsseln machen.

    Verschachtelt geschrieben, weil das die Kataloge für Übersetzer lesbar
    hält; flach benutzt, weil das im Code kürzer ist.
    """
    out = {}
    for key, value in data.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{full}."))
        else:
            out[full] = str(value)
    return out


def _fill(text: str, params: dict) -> str:
    """Platzhalter ersetzen, ohne bei fehlenden Werten zu scheitern.

    str.format() wirft bei einem unbekannten Platzhalter eine Ausnahme und
    würde damit wegen eines Übersetzungsfehlers die Anzeige zerlegen. Hier
    bleibt ein unbekannter Platzhalter einfach stehen.
    """
    if not params:
        return text

    def swap(match):
        name = match.group(1)
        if name in params:
            return str(params[name])
        return match.group(0)

    return _PLACEHOLDER.sub(swap, text)


# Modulweite Instanz – die Kataloge sind klein und ändern sich zur Laufzeit
# nur beim ausdrücklichen Neuladen.
translator = Translator()


def t(key: str, lang: str | None = None, **params) -> str:
    return translator.t(key, lang, **params)
