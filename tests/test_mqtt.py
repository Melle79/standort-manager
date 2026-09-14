"""Prüft, was bei MQTT herauskommt.

Zwei Dinge, die schon einmal falsch waren:
  - Entitäts-IDs dürfen beim Sprachwechsel nicht wandern, sonst bricht
    jede Automation.
  - Entitäts-IDs dürfen auch beim Umbenennen eines Geräts nicht wandern,
    und sie müssen lesbar sein - aus Apples Base64-Geräte-ID gebaut sind
    sie es nicht.
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
DATA = tempfile.mkdtemp(prefix="stm-mqtt-")
os.environ.update(DATA_DIR=DATA, TRANSLATIONS_DIR=f"{BASE}/translations")
sys.path.insert(0, f"{BASE}/app")

from mqtt_publisher import MqttPublisher, entity_slug
from store import store


class FakeClient:
    def __init__(self):
        self.nachrichten = []

    def publish(self, topic, payload, retain=False):
        self.nachrichten.append((topic, payload))


pub = MqttPublisher()
pub.client = FakeClient()
pub.connected = True

# So sieht eine echte Apple-Geraete-ID aus.
GERAET = "AepuztvyaFb6hyKYY+iruBG6Y797HOgwjurY+cFzW36TQdn36xUQeXCb8UFnhdEz"
store.set_device(GERAET, modus="track")

daten = {
    "id": GERAET, "name": "Handy A", "model": "iPhone 16 Pro Max",
    "device_class": "iPhone", "battery": 72, "battery_status": "NotCharging",
    "location": {"latitude": 52.3759, "longitude": 9.7320,
                 "accuracy": 12, "timestamp": 1789369000},
    "source": "icloud", "zone": "zone.home", "zone_name": "Zuhause",
    "state": "home", "distance_m": 0, "speed_ms": 0.2, "direction": "NE",
    "last_seen": 1789369000, "interval": 900,
    "address": {"display": "Musterweg 1, Musterstadt"},
    "travel": None, "offline": False,
}


def veroeffentliche(sprache):
    store.set("sprache", sprache)
    pub.client.nachrichten.clear()
    pub.republish_all([daten])
    return [(t, json.loads(p)) for t, p in pub.client.nachrichten
            if t.endswith("/config")]


print("1. Entitäts-IDs müssen lesbar sein")
configs = veroeffentliche("de")
tracker = next(c for t, c in configs if "/device_tracker/" in t)
print("   object_id:", tracker["object_id"])
assert "handy_a" in tracker["object_id"], "Kurzform enthält den Namen nicht"
assert "aepuztvy" not in tracker["object_id"].lower(), "Apple-ID steckt in der Entitäts-ID"

print("1b. Entitäts-ID muss festgelegt sein, nicht nur gewünscht")
for topic, config in configs:
    assert "default_entity_id" in config, (
        f"{topic} ohne default_entity_id - Home Assistant baut die ID sonst "
        "selbst aus Geräte- und Entitätsname")
    assert config["default_entity_id"].split(".")[1] == config["object_id"], (
        f"object_id und default_entity_id widersprechen sich in {topic}")
print("   alle", len(configs), "Nachrichten legen ihre Entitäts-ID fest")

print("2. Kein via_device ins Leere")
for topic, config in configs:
    assert "via_device" not in config.get("device", {}), (
        f"via_device in {topic} - Home Assistant legt das Gerät dann nicht an")
print("   kein via_device in", len(configs), "Discovery-Nachrichten")

print("3. Sprachwechsel ändert Namen, nicht IDs")
ids_de = {c["unique_id"] for _, c in configs}
namen_de = {c["name"] for _, c in configs}
configs_en = veroeffentliche("en")
ids_en = {c["unique_id"] for _, c in configs_en}
namen_en = {c["name"] for _, c in configs_en}
assert ids_de == ids_en, f"IDs gewandert: {ids_de ^ ids_en}"
assert namen_de != namen_en, "Namen haben sich nicht übersetzt"
print(f"   {len(ids_de)} IDs gleich, Namen übersetzt "
      f"(z.B. {sorted(namen_de)[0]!r} -> {sorted(namen_en)[0]!r})")

print("4. Umbenennen ändert die Entitäts-ID nicht")
vorher = entity_slug(GERAET, "Handy A")
nachher = entity_slug(GERAET, "Ganz anderer Name")
assert vorher == nachher, f"Kurzform gewandert: {vorher} -> {nachher}"
print(f"   bleibt {vorher!r}, auch nach dem Umbenennen")

print("5. Ohne echten Namen wird keine Kurzform festgeschrieben")
NEU = "AnderesGeraetOhneNamenYZ=="
notform = entity_slug(NEU, NEU)          # Name == ID: iCloud war noch nicht da
assert not store.get("entity_slugs", NEU), (
    "Notform wurde festgeschrieben - die Entität hieße für immer nach der Apple-ID")
richtig = entity_slug(NEU, "Handy der Tochter")
assert richtig == "handy_der_tochter", richtig
assert store.get("entity_slugs", NEU) == "handy_der_tochter"
print(f"   Notform {notform[:18]}... verworfen, später {richtig!r} festgeschrieben")

print("6. Zwei Geräte gleichen Namens kollidieren nicht")
zweite = entity_slug("EineAndereAppleID==", "Handy A")
assert zweite != vorher, "Zweites Gerät bekam dieselbe Kurzform"
print(f"   zweites Gerät bekommt {zweite!r}")

shutil.rmtree(DATA, ignore_errors=True)
print("\nAlle Prüfungen bestanden.")
