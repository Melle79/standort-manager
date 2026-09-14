#!/usr/bin/env python3
"""Prüft die Sprachkataloge gegen den Code.

Drei Fragen, die man vor jedem Release beantwortet haben will:
  1. Benutzt der Code einen Schlüssel, den kein Katalog kennt?
     -> In der Oberfläche erscheint dann der nackte Schlüssel.
  2. Fehlt einer Sprache ein Schlüssel, den eine andere hat?
     -> Die Rückfallsprache springt ein, die Stelle bleibt englisch.
  3. Liegt ein Schlüssel im Katalog, den niemand mehr benutzt?
     -> Karteileiche, die Übersetzer unnötig Zeit kostet.

Aufruf:  python3 tools/check_translations.py [--strict]
"""

import json
import re
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
KATALOGE = WURZEL / "standort_manager" / "translations"
QUELLEN = [
    WURZEL / "standort_manager" / "app",
    WURZEL / "standort_manager" / "www",
]

# So wird im Code auf Schlüssel verwiesen.
MUSTER = [
    # Python: t("key"), translator.t('key'), events.add("key", ...)
    re.compile(r"""\bt\(\s*["']([a-z0-9_]+(?:\.[a-z0-9_]+)+)["']"""),
    re.compile(r"""\badd\(\s*["']([a-z0-9_]+(?:\.[a-z0-9_]+)+)["']"""),
    # JavaScript: I18n.t('key')
    re.compile(r"""I18n\.t\(\s*["'`]([a-z0-9_]+(?:\.[a-z0-9_]+)+)["'`]"""),
    # HTML: data-i18n="key"
    re.compile(r"""data-i18n(?:-placeholder|-title|-aria)?=["']([a-z0-9_.]+)["']"""),
    # Sensortabelle und ähnliche Listen: "sensor.distance" als Wert
    re.compile(r"""["'](sensor\.[a-z_]+|state\.[a-z_]+|error\.[a-z_]+|device\.[a-z_]+)["']"""),
]

# Schlüssel, die zur Laufzeit zusammengesetzt werden – die findet kein
# Muster, also stehen sie hier.
DYNAMISCH = {
    "state.home", "state.away", "state.arriving", "state.leaving",
    "state.traveling", "state.stationary", "state.in_zone",
    "state.offline", "state.unknown",
    "device.mode_track", "device.mode_monitor", "device.mode_inactive",
    "device.mode_track_hint", "device.mode_monitor_hint",
    "device.mode_inactive_hint",
    "device.source_icloud", "device.source_companion", "device.source_none",
    "event.level_info", "event.level_warning", "event.level_error",
    "event.level_debug",
}


def flach(daten: dict, prefix: str = "") -> dict:
    out = {}
    for schluessel, wert in daten.items():
        voll = f"{prefix}{schluessel}"
        if isinstance(wert, dict):
            out.update(flach(wert, f"{voll}."))
        else:
            out[voll] = wert
    return out


def lade_kataloge() -> dict:
    kataloge = {}
    for pfad in sorted(KATALOGE.glob("*.json")):
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        kataloge[pfad.stem] = flach(daten.get("strings", {}))
    return kataloge


def sammle_verwendete() -> set:
    verwendet = set(DYNAMISCH)
    for ordner in QUELLEN:
        for pfad in ordner.rglob("*"):
            if pfad.suffix not in (".py", ".js", ".html"):
                continue
            text = pfad.read_text(encoding="utf-8")
            for muster in MUSTER:
                verwendet.update(muster.findall(text))
    return verwendet


def main() -> int:
    streng = "--strict" in sys.argv
    kataloge = lade_kataloge()
    if not kataloge:
        print("Keine Kataloge gefunden.")
        return 1

    verwendet = sammle_verwendete()
    alle_schluessel = set().union(*kataloge.values())
    probleme = 0

    print(f"Kataloge: {', '.join(sorted(kataloge))}")
    print(f"Schlüssel insgesamt: {len(alle_schluessel)}")
    print(f"Im Code verwendet:  {len(verwendet)}\n")

    # 1. Im Code benutzt, aber nirgends übersetzt
    fehlend = sorted(k for k in verwendet if k not in alle_schluessel)
    if fehlend:
        probleme += len(fehlend)
        print(f"FEHLT IM KATALOG ({len(fehlend)}):")
        for schluessel in fehlend:
            print(f"  {schluessel}")
        print()

    # 2. Lücken zwischen den Sprachen
    for sprache, katalog in sorted(kataloge.items()):
        luecken = sorted(alle_schluessel - set(katalog))
        if luecken:
            probleme += len(luecken)
            print(f"FEHLT IN {sprache} ({len(luecken)}):")
            for schluessel in luecken:
                print(f"  {schluessel}")
            print()

    # 3. Karteileichen
    unbenutzt = sorted(alle_schluessel - verwendet)
    if unbenutzt:
        print(f"Nirgends verwendet ({len(unbenutzt)}):")
        for schluessel in unbenutzt:
            print(f"  {schluessel}")
        print("  (kein Fehler – nur ein Hinweis auf mögliche Altlasten)\n")

    # 4. Platzhalter müssen in allen Sprachen gleich sein, sonst steht in
    #    einer Sprache plötzlich {name} im Text.
    platzhalter_fehler = 0
    muster = re.compile(r"\{(\w+)\}")
    for schluessel in sorted(alle_schluessel):
        erwartet = None
        for sprache, katalog in kataloge.items():
            if schluessel not in katalog:
                continue
            gefunden = set(muster.findall(katalog[schluessel]))
            if erwartet is None:
                erwartet = gefunden
            elif gefunden != erwartet:
                print(f"PLATZHALTER UNGLEICH: {schluessel}")
                print(f"  erwartet {sorted(erwartet)}, in {sprache} {sorted(gefunden)}")
                platzhalter_fehler += 1
    if platzhalter_fehler:
        probleme += platzhalter_fehler
        print()

    if probleme:
        print(f"Ergebnis: {probleme} Problem(e).")
        return 1 if streng else 0
    print("Ergebnis: alles sauber.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
