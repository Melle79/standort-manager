"""Standort-Manager – Oberfläche und API.

Flask liefert die Ingress-Oberfläche aus und stellt die API bereit. Die
eigentliche Arbeit passiert im Tracker-Thread; hier wird nur gelesen,
eingestellt und angestoßen.

Alle Antworten, die Text enthalten, liefern Schlüssel statt Sätze. Die
Oberfläche übersetzt selbst – so bleibt die API sprachfrei und ein zweiter
Client (etwa eine Dashboard-Karte) kann dieselbe Quelle anders darstellen.
"""

import logging
import os
import signal
import sys
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

from apple import account
from events import events
from fahrten import fahrtenbuch
from i18n import translator
import lovelace
from mqtt_publisher import publisher
from store import DEFAULTS, store
import ha
import tracking
from tracking import tracker

VERSION = os.environ.get("ADDON_VERSION", "0.1.0")
WWW_DIR = os.environ.get("WWW_DIR", "/www")
PORT = int(os.environ.get("PORT", "8097"))

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("standort")

app = Flask(__name__, static_folder=None)


# ------------------------------------------------------------------ Oberfläche

@app.route("/")
def index():
    return send_from_directory(WWW_DIR, "index.html")


@app.route("/<path:pfad>")
def statisch(pfad):
    return send_from_directory(WWW_DIR, pfad)


# ------------------------------------------------------------------- Sprachen

@app.route("/api/languages")
def languages():
    return jsonify(
        {
            "available": translator.languages,
            "current": store.get("sprache", default="de"),
            "fallback": "en",
        }
    )


@app.route("/api/i18n/<lang>")
def catalog(lang):
    """Kompletter Katalog für die Oberfläche, inklusive Rückfallsprache."""
    return jsonify(
        {
            "lang": translator.resolve(lang),
            "strings": translator.catalog_for_ui(lang),
        }
    )


# --------------------------------------------------------------------- Status

@app.route("/api/status")
def status():
    return jsonify(
        {
            "version": VERSION,
            "apple": account.status(),
            "mqtt": publisher.status(),
            "homeassistant": {"available": ha.available()},
            "language": store.get("sprache", default="de"),
            "units": store.get("einheiten", default="metrisch"),
            "time_format": store.get("zeitformat", default="24h"),
            "devices": len(tracker.devices),
            "last_cycle": tracker.last_cycle,
            "paused": tracker.paused,
            "trips": fahrtenbuch.statistik(),
        }
    )


# ------------------------------------------------------------------ Anmeldung

@app.route("/api/account/login", methods=["POST"])
def login():
    daten = request.get_json(silent=True) or {}
    username = (daten.get("username") or "").strip()
    password = daten.get("password") or ""
    merken = bool(daten.get("store_password", True))
    if not username or not password:
        return jsonify({"error_key": "error.bad_credentials"}), 400
    ergebnis = account.login(username, password, store_password=merken)
    if ergebnis["state"] == "ready":
        ha.meldung_zuruecknehmen()
        tracker.poll_now()
    return jsonify(ergebnis)


@app.route("/api/account/2fa", methods=["POST"])
def twofactor():
    daten = request.get_json(silent=True) or {}
    code = (daten.get("code") or "").strip()
    if not code:
        return jsonify({"error_key": "error.bad_code"}), 400
    ergebnis = account.submit_2fa(daten.get("username"), code,
                                  trust=bool(daten.get("trust", True)))
    if ergebnis["state"] == "ready":
        # Das Problem ist behoben - der Hinweis in Home Assistant kann weg.
        ha.meldung_zuruecknehmen()
        tracker.poll_now()
    return jsonify(ergebnis)


@app.route("/api/account/resend", methods=["POST"])
def resend():
    daten = request.get_json(silent=True) or {}
    return jsonify({"ok": account.request_new_code(daten.get("username"))})


@app.route("/api/account/logout", methods=["POST"])
def logout():
    daten = request.get_json(silent=True) or {}
    account.logout(daten.get("username"), forget=bool(daten.get("forget")))
    return jsonify(account.status())


@app.route("/api/accounts")
def accounts():
    """Alle eingerichteten Apple-Konten mit ihrem Zustand."""
    return jsonify(account.status().get("accounts", []))


@app.route("/api/accounts/<path:username>", methods=["DELETE"])
def account_entfernen(username):
    """Ein Konto abmelden und vergessen.

    Die Geräte dieses Kontos verschwinden beim nächsten Zyklus aus der
    Liste; ihre Entitäten werden vorher abgemeldet, damit keine
    Karteileichen zurückbleiben.
    """
    betroffen = [
        did for did, konto in account._zuordnung.items() if konto == username
    ]
    for device_id in betroffen:
        publisher.remove_device(device_id)
        tracker.devices.pop(device_id, None)
    account.logout(username, forget=True)
    events.add("event.config_changed", "info", setting=username)
    return jsonify({"ok": True, "removed_devices": len(betroffen)})


# --------------------------------------------------------------------- Geräte

@app.route("/api/devices")
def devices():
    zustaende = tracker.snapshot()
    for eintrag in zustaende:
        eintrag["config"] = store.device(eintrag["id"])
    return jsonify(zustaende)


@app.route("/api/devices/<path:device_id>", methods=["POST"])
def device_config(device_id):
    daten = request.get_json(silent=True) or {}
    erlaubt = {"modus", "name", "person", "companion_entity",
               "reference_zone", "timezone_offset", "bild"}
    felder = {k: v for k, v in daten.items() if k in erlaubt}

    if felder.get("modus") not in (None, "track", "monitor", "inactive"):
        return jsonify({"error_key": "error.save_failed"}), 400

    vorher = store.device(device_id)["modus"]
    store.set_device(device_id, **felder)
    # Der Gerätename, nicht die Apple-ID: die ist ein Base64-Block und
    # macht das Protokoll unlesbar.
    events.add("event.config_changed", "info", device_id=device_id,
               setting=_geraetename(device_id))

    # Von 'track' weg: Entitäten abmelden, sonst bleiben Karteileichen.
    if vorher == "track" and felder.get("modus") in ("inactive", "monitor"):
        publisher.remove_device(device_id)
    if felder.get("modus") == "track":
        tracker.poll_now()

    return jsonify(store.device(device_id))


@app.route("/api/poll", methods=["POST"])
def poll():
    tracker.poll_now()
    return jsonify({"ok": True})


# -------------------------------------------------------------------- Befehle

def _geraetename(device_id: str) -> str:
    zustand = tracker.get(device_id)
    return (zustand.name if zustand else None) or device_id


def _befehl_ausfuehren(device_id: str, befehl: str, text: str | None = None) -> dict:
    """Einen Gerätebefehl ausführen und das Ergebnis protokollieren.

    Gemeinsamer Weg für die eigene Oberfläche und die Schaltflächen in
    Home Assistant, damit beide dasselbe tun und dasselbe melden.
    """
    lang = store.get("sprache", default="de")
    name = _geraetename(device_id)

    if befehl == "locate":
        tracker.poll_now()
        events.add("event.command_sent", "info", device_id=device_id,
                   device=name, command=translator.t("control.locate", lang))
        return {"ok": True}

    if befehl == "sound":
        ergebnis = account.play_sound(device_id)
        bezeichnung = translator.t("control.play_sound", lang)
    elif befehl == "message_send":
        nachricht = text or translator.t("control.message_default", lang)
        ergebnis = account.display_message(device_id, nachricht)
        bezeichnung = translator.t("control.send_message", lang)
    else:
        return {"ok": False, "error_key": "error.generic"}

    if ergebnis.get("ok"):
        events.add("event.command_sent", "info", device_id=device_id,
                   device=name, command=bezeichnung)
    else:
        events.add("event.command_failed", "warning", device_id=device_id,
                   device=name, command=bezeichnung,
                   reason=ergebnis.get("error_key", "error.generic"))
    return ergebnis


def _mqtt_befehl(device_id: str | None, befehl: str, nutzlast: str | None) -> None:
    """Eine Schaltfläche in Home Assistant wurde betätigt."""
    if device_id is None:
        if befehl == "tracking":
            tracker.set_paused(nutzlast == "OFF")
        elif befehl == "locate_all":
            tracker.poll_now()
        return
    _befehl_ausfuehren(device_id, befehl, nutzlast)


@app.route("/api/devices/<path:device_id>/command", methods=["POST"])
def device_command(device_id):
    daten = request.get_json(silent=True) or {}
    befehl = daten.get("befehl")
    if befehl in ("locate", "sound", "message_send"):
        return jsonify(_befehl_ausfuehren(device_id, befehl, daten.get("text")))

    if befehl == "lost":
        # Sperrt das Gerät - nur über die Oberfläche, nie über eine
        # Schaltfläche, die versehentlich getroffen werden kann.
        nummer = (daten.get("nummer") or "").strip()
        if not nummer:
            return jsonify({"ok": False, "error_key": "error.generic"}), 400
        lang = store.get("sprache", default="de")
        ergebnis = account.lost_device(
            device_id, nummer,
            daten.get("text") or translator.t("control.message_default", lang))
        name = _geraetename(device_id)
        bezeichnung = translator.t("control.lost_mode", lang)
        if ergebnis.get("ok"):
            events.add("event.command_sent", "warning", device_id=device_id,
                       device=name, command=bezeichnung)
        else:
            events.add("event.command_failed", "error", device_id=device_id,
                       device=name, command=bezeichnung,
                       reason=ergebnis.get("error_key", "error.generic"))
        return jsonify(ergebnis)

    return jsonify({"ok": False, "error_key": "error.generic"}), 400


@app.route("/api/tracking", methods=["POST"])
def tracking_umschalten():
    daten = request.get_json(silent=True) or {}
    tracker.set_paused(bool(daten.get("paused")))
    return jsonify({"paused": tracker.paused})


# ---------------------------------------------------------------------- Zonen

@app.route("/api/zones")
def zones():
    """Zonen aus Home Assistant, ergänzt um unsere eigene Einstellung
    und darum, wer gerade dort ist."""
    eigene = store.get("zonen", default={}) or {}
    belegung = {}
    for zustand in tracker.snapshot():
        if zustand.get("zone") and store.device(zustand["id"])["modus"] == "track":
            belegung.setdefault(zustand["zone"], []).append(zustand["name"])

    out = []
    for zone in ha.zones(force=request.args.get("force") == "1"):
        eintrag = dict(zone)
        eintrag["aktiv"] = (
            True if zone["entity_id"] == "zone.home"
            else eigene.get(zone["entity_id"], {}).get("aktiv", True)
        )
        eintrag["fixed"] = zone["entity_id"] == "zone.home"
        eintrag["occupants"] = belegung.get(zone["entity_id"], [])
        out.append(eintrag)
    return jsonify(out)


@app.route("/api/zones/<path:entity_id>", methods=["POST"])
def zone_setzen(entity_id):
    """Eine Zone für die Erkennung ein- oder ausschalten."""
    if entity_id == "zone.home":
        return jsonify({"error_key": "zone.home_fixed"}), 400
    daten = request.get_json(silent=True) or {}
    store.set("zonen", entity_id, {"aktiv": bool(daten.get("aktiv", True))})
    name = next((z["name"] for z in ha.zones() if z["entity_id"] == entity_id),
                entity_id)
    events.add("event.config_changed", "info", setting=name)
    tracker.poll_now()
    return jsonify({"ok": True, "aktiv": bool(daten.get("aktiv", True))})


@app.route("/api/trackers")
def trackers():
    """device_tracker-Entitäten zur Auswahl als Companion-Quelle."""
    return jsonify(ha.device_trackers())


# ------------------------------------------------------------------ Protokoll

@app.route("/api/events")
def event_list():
    lang = request.args.get("lang") or store.get("sprache", default="de")
    return jsonify(
        events.list(
            lang=lang,
            level=request.args.get("level"),
            device=request.args.get("device"),
            limit=min(int(request.args.get("limit", 200)), 1000),
        )
    )


@app.route("/api/events", methods=["DELETE"])
def event_clear():
    events.clear()
    return jsonify({"ok": True})


# --------------------------------------------------------------- Einstellungen

@app.route("/api/settings")
def settings_get():
    return jsonify(store.all())


@app.route("/api/settings", methods=["POST"])
def settings_set():
    daten = request.get_json(silent=True) or {}
    sprache_vorher = store.get("sprache", default="de")

    for schluessel in ("sprache", "einheiten", "zeitformat", "entity_praefix",
                       "genauigkeitsschwelle", "durchfahrt_sekunden",
                       "benachrichtigung"):
        if schluessel in daten:
            store.set(schluessel, daten[schluessel])

    for gruppe in ("intervalle", "geocode", "route"):
        if gruppe in daten and isinstance(daten[gruppe], dict):
            for schluessel, wert in daten[gruppe].items():
                if schluessel in (DEFAULTS.get(gruppe) or {}):
                    store.set(gruppe, schluessel, wert)

    sprache = store.get("sprache", default="de")
    if sprache != sprache_vorher:
        name = next(
            (l["name"] for l in translator.languages if l["code"] == sprache), sprache
        )
        events.add("event.language_changed", "info", language=name)
        # Entitätsnamen tragen die Sprache – also neu anmelden.
        publisher.republish_all(
            [tracker.get(d["id"]) for d in tracker.snapshot() if tracker.get(d["id"])]
        )
    else:
        events.add("event.config_changed", "info", setting="settings.title")

    return jsonify(store.all())


@app.route("/api/notify_services")
def notify_services():
    return jsonify(ha.notify_dienste())


@app.route("/api/reset", methods=["POST"])
def reset():
    for device_id in list(tracker.devices):
        publisher.remove_device(device_id)
    account.logout(forget=True)
    for schluessel in list(store.all()):
        store.set(schluessel, DEFAULTS.get(schluessel))
    events.clear()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------- Start

def _shutdown(signum, frame):
    events.add("event.shutdown", "info")
    events.save()
    tracker.stop()
    publisher.stop()
    sys.exit(0)


def main():
    events.add("event.startup", "info", version=VERSION)

    # Karte ausliefern und anmelden, bevor der Rest anlaeuft - beides ist
    # schnell und darf den Start nicht ueberleben, wenn es scheitert.
    try:
        lovelace.einrichten(VERSION)
    except Exception as err:  # noqa: BLE001
        log.warning("Dashboard-Karte nicht eingerichtet: %s", err)

    publisher.on_command = _mqtt_befehl
    publisher.start()
    tracker.set_publisher(publisher)

    # Gespeicherte Apple-Sitzung wiederaufnehmen, bevor der Takt anläuft.
    account.restore()
    tracker.start()
    # Die Brücke erst nach dem Verbinden anmelden; start() ist asynchron.
    threading.Timer(5.0, publisher.publish_bridge).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
