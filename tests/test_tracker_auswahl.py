"""Als Companion-Quelle nur Tracker der Companion-App anbieten.

Bis 0.6.3 standen in der Auswahl alle device_tracker: iBeacons an den
Mülltonnen, Router-Tracker, Shelly-Relais, Steckdosen.
"""
import json
import os
import pathlib
import sys
import tempfile

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
os.environ.update(DATA_DIR=tempfile.mkdtemp(prefix="stm-tracker-"),
                  TRANSLATIONS_DIR=f"{BASE}/translations",
                  SUPERVISOR_TOKEN="test")
sys.path.insert(0, f"{BASE}/app")

import ha  # noqa: E402


def zustand(eid, name, source_type):
    return {"entity_id": eid, "state": "home",
            "attributes": {"friendly_name": name, "source_type": source_type}}


ha.states = lambda entity_id=None: [
    zustand("device_tracker.handy_a", "Handy A", "gps"),
    zustand("device_tracker.biotonne", "Biotonne-iBeacon", "bluetooth_le"),
    zustand("device_tracker.steckdose", "Steckdose", "router"),
    zustand("device_tracker.fremd_gps", "Anderes GPS", "gps"),
    {"entity_id": "device_tracker.standort_handy_a", "state": "home",
     "attributes": {"friendly_name": "Eigen", "source_type": "gps",
                    "attribution": "Standort-Manager"}},
    zustand("sensor.irgendwas", "Sensor", None),
]


class Antwort:
    def __init__(self, text, ok=True):
        self.text, self.ok = text, ok

    def raise_for_status(self):
        if not self.ok:
            raise ha.requests.RequestException("kaputt")


fehler = []

ha.requests.post = lambda url, **kw: Antwort(json.dumps(
    ["device_tracker.handy_a", "sensor.handy_a_akku"]))
ids = [t["entity_id"] for t in ha.device_trackers()]
if ids != ["device_tracker.handy_a"]:
    fehler.append(f"mit Template-API: {ids}")

# Ohne Template-API: wenigstens nur GPS-Tracker.
ha.requests.post = lambda url, **kw: Antwort("", ok=False)
ids = [t["entity_id"] for t in ha.device_trackers()]
if ids != ["device_tracker.handy_a", "device_tracker.fremd_gps"]:
    fehler.append(f"Notbehelf: {ids}")

if fehler:
    print("FEHLER:"); [print("  -", f) for f in fehler]; sys.exit(1)
print("Tracker-Auswahl: alle Erwartungen erfüllt.")
