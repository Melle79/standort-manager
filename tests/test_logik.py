"""Durchspielen einer Fahrt: zuhause -> unterwegs -> Zielzone."""
import os, pathlib, sys, time, json, shutil, tempfile

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
DATA = tempfile.mkdtemp(prefix="stm-test-")
os.environ.update(DATA_DIR=DATA, TRANSLATIONS_DIR=f"{BASE}/translations",
                  MQTT_ENABLED="false")
sys.path.insert(0, f"{BASE}/app")

import i18n, store as store_mod, events as events_mod
import ha, geocode, route, tracking
from tracking import tracker, distance_m
from events import events
from store import store

# --- Attrappen: keine echten Dienste im Test ---------------------------
# Frei erfundene Koordinaten, rund 10 km auseinander. Gebraucht wird nur
# eine plausible Strecke, kein wirklicher Ort.
HEIMAT = (52.3759, 9.7320)          # "Zuhause"
BUERO  = (52.4400, 9.8150)          # zweite Zone

ha.zones = lambda force=False: [
    {"entity_id": "zone.home", "name": "Zuhause", "latitude": HEIMAT[0],
     "longitude": HEIMAT[1], "radius": 100, "passive": False},
    {"entity_id": "zone.buero", "name": "Büro", "latitude": BUERO[0],
     "longitude": BUERO[1], "radius": 150, "passive": False},
]
ha.tracker_location = lambda eid: None
geocode.reverse = lambda lat, lon, lang="de": {
    "display": f"Musterweg 1, {'Musterstadt'}"}
route.travel_time = lambda a, b, c, d: {
    "duration_seconds": 1260, "distance_meters": 14200,
    "arrival_timestamp": time.time() + 1260}

GERAET = "geraet_a"
store.set_device(GERAET, modus="track", name="Handy A")

punkte = [
    ("zuhause",      HEIMAT[0],        HEIMAT[1],        0),
    ("losgefahren",  52.3810,             9.7390,             120),
    ("unterwegs",    52.4020,             9.7670,             240),
    ("fast da",      52.4330,             9.8060,             360),
    ("im Büro",      BUERO[0],            BUERO[1],            480),
]

start = time.time()
protokoll = []
for name, lat, lon, versatz in punkte:
    jetzt = start + versatz
    tracking.time.time = lambda j=jetzt: j     # Zeit kontrollieren
    tracker.zones = ha.zones()
    zustand = tracker.devices.setdefault(GERAET, tracking.DeviceState(GERAET))
    zustand.name = "Handy A"
    tracker._update_location(zustand, {
        "latitude": lat, "longitude": lon, "accuracy": 20,
        "timestamp": jetzt, "is_old": False, "finished": True,
    }, "icloud", jetzt)
    tracker._enrich(zustand)
    tracker._schedule(zustand, jetzt)
    protokoll.append((name, zustand.state, zustand.zone_name,
                      zustand.distance_m, zustand.speed_ms, zustand.interval,
                      zustand.heading))

print("Schritt        Zustand      Zone      Entfernung   Tempo      Takt    Richtung")
print("-" * 84)
for name, st, zone, dist, speed, takt, heading in protokoll:
    print(f"{name:<14} {st:<12} {str(zone or '–'):<9} "
          f"{(str(round(dist/1000,1))+' km') if dist else '–':<12} "
          f"{(str(round(speed*3.6))+' km/h') if speed else '–':<10} "
          f"{str(takt)+'s':<7} {heading}")

# --- Erwartungen pruefen ----------------------------------------------
fehler = []
if protokoll[0][1] != "home": fehler.append("Start muss 'home' sein")
if protokoll[0][2] != "Zuhause": fehler.append("Startzone muss 'Zuhause' sein")
if protokoll[-1][2] != "Büro": fehler.append("Ziel muss Zone 'Büro' sein")
if protokoll[0][5] <= protokoll[2][5]: fehler.append("Takt zuhause muss traeger sein als unterwegs")
if not any(p[1] == "traveling" for p in protokoll): fehler.append("Kein 'traveling' erkannt")

# Fahrtrichtung: wer von zuhause wegfaehrt, muss sich entfernen
richtungen = [p[6] for p in protokoll]
if "away" not in richtungen:
    fehler.append(f"Keine Wegfahrt erkannt, Richtungen: {richtungen}")
if any(r == "towards" for r in richtungen[1:4]):
    fehler.append("Annaeherung erkannt, obwohl das Geraet sich entfernt")

print("\n=== Protokoll deutsch ===")
for e in events.list("de", level="debug", limit=10)[:8]:
    print(" ", e["text"])
print("=== dieselben Ereignisse englisch ===")
for e in events.list("en", level="debug", limit=10)[:8]:
    print(" ", e["text"])

print("\n=== Entfernungsrechnung gegenprobe ===")
d = distance_m(*HEIMAT, *BUERO)
print(f"  Zuhause -> Büro: {d/1000:.1f} km Luftlinie (erwartet ~10 km)")
if not (8000 < d < 13000): fehler.append(f"Entfernung unplausibel: {d}")

shutil.rmtree(DATA, ignore_errors=True)
print()
if fehler:
    print("FEHLER:"); [print("  -", f) for f in fehler]; sys.exit(1)
print("Alle Erwartungen erfüllt.")
