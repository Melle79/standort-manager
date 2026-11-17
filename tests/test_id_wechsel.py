"""Neues iPhone, neue Apple-Geräte-ID.

Wer per Backup auf ein neues iPhone umzieht, behält den Gerätenamen, aber
Apple liefert das Gerät unter einer neuen ID. Bis 0.6.1 hing alles
Eingerichtete an der alten: Die Übersicht zeigte eine Kachel mit dem
Base64-Block, „zuletzt gesehen: nie“, und das neue Gerät stand auf inaktiv.
"""
import os, pathlib, sys, tempfile

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
DATA = tempfile.mkdtemp(prefix="stm-idw-")
os.environ.update(DATA_DIR=DATA, TRANSLATIONS_DIR=f"{BASE}/translations",
                  MQTT_ENABLED="false")
sys.path.insert(0, f"{BASE}/app")

import ha, tracking
from events import events
from store import store
from tracking import tracker

ha.zones = lambda force=False: []
ha.tracker_location = lambda eid: None
tracking.account.fetch = lambda locate=True: GELIEFERT

ALT, NEU, FREMD = "Aalt+id==", "Aneu+id==", "Afremd+id=="
store.set_device(ALT, modus="track", companion_entity="device_tracker.handy_a")
store.set("entity_slugs", ALT, "handy_von_a")
# Die Companion-App legt den Zustand der alten ID schon vor iCloud an.
tracker.devices[ALT] = tracking.DeviceState(ALT)

GELIEFERT = [
    {"id": NEU, "name": "Handy von A", "model_name": "iPhone"},
    {"id": FREMD, "name": "Handy von B", "model_name": "iPhone"},
]
tracker._due = lambda jetzt: True
tracker.cycle()

fehler = []
if store.device(NEU)["modus"] != "track":
    fehler.append("neue ID muss den Modus übernehmen")
if store.device(NEU)["companion_entity"] != "device_tracker.handy_a":
    fehler.append("Companion-Tracker muss mitwandern")
if store.get("entity_slugs", NEU) != "handy_von_a":
    fehler.append("Kurzform muss mitwandern, sonst neue Entitäten in HA")
if store.get("entity_slugs", ALT) or store.get("geraete", ALT):
    fehler.append("alte ID muss aus der Konfiguration verschwinden")
if ALT in tracker.devices:
    fehler.append("Geisterkachel der alten ID muss weg")
if store.device(FREMD)["modus"] != "inactive":
    fehler.append("fremdes Gerät darf nichts erben")
if not any(e["key"] == "event.device_id_changed" for e in events.list("de")):
    fehler.append("Wechsel muss im Protokoll stehen")

# Zwei Kandidaten gleichen Namens: lieber nichts tun als raten.
ALT2 = "Aalt2=="
store.set_device(ALT2, modus="track")
store.set("entity_slugs", ALT2, "ipad")
GELIEFERT = [{"id": "Ax==", "name": "iPad"}, {"id": "Ay==", "name": "iPad"}]
tracker._due = lambda jetzt: True
tracker._due = lambda jetzt: True
tracker.cycle()
if store.get("entity_slugs", ALT2) != "ipad":
    fehler.append("bei zwei Kandidaten darf nichts wandern")

if fehler:
    print("FEHLER:"); [print("  -", f) for f in fehler]; sys.exit(1)
print("ID-Wechsel: alle Erwartungen erfüllt.")
