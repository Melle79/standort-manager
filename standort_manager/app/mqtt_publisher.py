"""Veröffentlichung nach Home Assistant über MQTT-Discovery.

Sprachfrage an dieser Stelle: Die *Namen* der Entitäten werden übersetzt,
ihre *IDs* nicht. Sonst würde ein Sprachwechsel sämtliche Entitäts-IDs
ändern und jede Automation stillschweigend ins Leere laufen lassen – genau
die Falle, die schon bei Geräte-IDs zuschlägt.

Wechselt der Benutzer die Sprache, wird die Discovery deshalb neu gesendet:
gleiche IDs, neue Anzeigenamen.
"""

import json
import logging
import os
import threading
import time

import paho.mqtt.client as mqtt

from events import events
from i18n import translator
from store import store

log = logging.getLogger("standort.mqtt")

DISCOVERY_PREFIX = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
KEEPALIVE = 60

# Sensoren je Gerät: (Schlüssel, Übersetzungsschlüssel, device_class,
# Einheit, Symbol, Kategorie)
SENSORS = [
    ("distance", "sensor.distance", "distance", "km", "mdi:map-marker-distance", None),
    ("travel_time", "sensor.travel_time", "duration", "min", "mdi:car-clock", None),
    ("arrival_time", "sensor.arrival_time", "timestamp", None, "mdi:clock-end", None),
    ("battery", "sensor.battery", "battery", "%", None, "diagnostic"),
    ("address", "sensor.address", None, None, "mdi:map-marker", None),
    ("zone", "sensor.zone", None, None, "mdi:map-marker-radius", None),
    ("last_seen", "sensor.last_seen", "timestamp", None, "mdi:clock-check", "diagnostic"),
    ("accuracy", "sensor.accuracy", "distance", "m", "mdi:crosshairs-gps", "diagnostic"),
    ("speed", "sensor.speed", "speed", "km/h", "mdi:speedometer", "diagnostic"),
    ("direction", "sensor.direction", None, None, "mdi:compass", "diagnostic"),
    ("interval", "sensor.interval", "duration", "s", "mdi:timer-sync", "diagnostic"),
    ("direction_home", "sensor.direction_home", None, None, "mdi:sign-direction", None),
    ("moved_distance", "sensor.moved_distance", "distance", "m",
     "mdi:map-marker-path", "diagnostic"),
    ("zone_distance", "sensor.zone_distance", "distance", "m",
     "mdi:map-marker-radius-outline", "diagnostic"),
    ("next_update", "sensor.next_update", "timestamp", None,
     "mdi:clock-start", "diagnostic"),
    ("battery_state", "sensor.battery_state", None, None,
     "mdi:battery-heart-variant", "diagnostic"),
]

# Nur bei Geräten mit Zeitversatz - sonst stünde überall dieselbe Uhrzeit
# wie in Home Assistant.
TIMEZONE_SENSOR = ("local_time", "sensor.local_time", None, None, "mdi:clock-outline")

# Sensoren, die nur entstehen, wenn eine zweite Bezugszone gewaehlt ist.
REFERENCE_SENSORS = [
    ("ref_distance", "sensor.distance_zone", "distance", "km",
     "mdi:map-marker-distance"),
    ("ref_travel_time", "sensor.travel_time_zone", "duration", "min",
     "mdi:car-clock"),
    ("ref_arrival_time", "sensor.arrival_time_zone", "timestamp", None,
     "mdi:clock-end"),
]

# Schaltflaechen je Geraet: (Schluessel, Uebersetzung, Symbol)
BUTTONS = [
    ("locate", "control.locate", "mdi:crosshairs-gps"),
    ("sound", "control.play_sound", "mdi:volume-high"),
    ("message_send", "control.send_message", "mdi:message-alert"),
]


class MqttPublisher:
    def __init__(self):
        self._lock = threading.Lock()
        self.client: mqtt.Client | None = None
        self.connected = False
        self.host = os.environ.get("MQTT_HOST", "")
        self.port = int(os.environ.get("MQTT_PORT") or 1883)
        self.user = os.environ.get("MQTT_USER", "")
        self.password = os.environ.get("MQTT_PASSWORD", "")
        self.enabled = os.environ.get("MQTT_ENABLED", "true").lower() == "true"
        self._announced: set[str] = set()
        self._bridge_announced = False
        # Wird von aussen gesetzt: nimmt (device_id, befehl, nutzlast) und
        # fuehrt ihn aus. Haelt den Publisher frei von Fachlogik.
        self.on_command = None
        # Nachrichtentexte je Geraet, gesetzt ueber die text-Entitaet.
        self._messages: dict[str, str] = {}

    # ----------------------------------------------------------- Verbindung

    def start(self) -> None:
        if not self.enabled or not self.host:
            events.add("event.mqtt_disabled", "warning")
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"standort-manager-{int(time.time())}")
        if self.user:
            client.username_pw_set(self.user, self.password)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # Verfügbarkeitsmeldung: Stirbt das Add-on, werden die Entitäten
        # als nicht verfügbar markiert statt auf dem letzten Wert zu stehen.
        client.will_set(self._availability_topic(), "offline", retain=True)
        self.client = client
        try:
            client.connect(self.host, self.port, KEEPALIVE)
            client.loop_start()
        except OSError as err:
            events.add("event.poll_failed", "error", reason=str(err)[:200])

    def stop(self) -> None:
        if not self.client:
            return
        try:
            self.client.publish(self._availability_topic(), "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as err:  # noqa: BLE001
            log.warning("MQTT-Abmeldung unsauber: %s", err)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        self.connected = True
        events.add("event.mqtt_connected", "info", host=self.host)
        client.publish(self._availability_topic(), "online", retain=True)
        # Alle Befehlskanaele auf einmal abonnieren.
        praefix = store.get("entity_praefix", default="standort")
        client.subscribe(f"{praefix}/+/+/set")
        # Nach einem Verbindungsabriss muss die Discovery erneut raus,
        # falls der Broker ohne Persistenz läuft.
        self._announced.clear()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        events.add("event.mqtt_disconnected", "warning")

    def _on_message(self, client, userdata, msg):
        """Eine Schaltflaeche in Home Assistant wurde betaetigt."""
        try:
            nutzlast = msg.payload.decode("utf-8", "replace")
            teile = msg.topic.split("/")
            if len(teile) != 4 or teile[3] != "set":
                return
            _, slug, befehl, _ = teile
        except Exception as err:  # noqa: BLE001
            log.warning("Unlesbare MQTT-Nachricht: %s", err)
            return

        if slug == "bridge":
            self._bridge_command(befehl, nutzlast)
            return

        device_id = self._device_for_slug(slug)
        if not device_id:
            log.warning("Befehl fuer unbekannte Kurzform: %s", slug)
            return

        if befehl == "message":
            # Reine Texteingabe, noch kein Versand.
            self._messages[device_id] = nutzlast
            self.client.publish(f"{self._base(slug)}/message", nutzlast, retain=True)
            return

        if self.on_command:
            self.on_command(device_id, befehl, self._messages.get(device_id))

    def _bridge_command(self, befehl: str, nutzlast: str) -> None:
        if self.on_command:
            self.on_command(None, befehl, nutzlast)

    def _device_for_slug(self, slug: str) -> str | None:
        for device_id, kurz in (store.get("entity_slugs", default={}) or {}).items():
            if kurz == slug:
                return device_id
        return None

    def _base(self, slug: str) -> str:
        praefix = store.get("entity_praefix", default="standort")
        return f"{praefix}/{slug}"

    # -------------------------------------------------------------- Ausgabe

    def publish_devices(self, zustaende) -> None:
        if not self.client or not self.connected:
            return
        neu = 0
        for zustand in zustaende:
            daten = zustand.as_dict() if hasattr(zustand, "as_dict") else zustand
            device_id = daten["id"]
            if store.device(device_id)["modus"] != "track":
                continue
            if device_id not in self._announced:
                self._announce(device_id, daten)
                self._announced.add(device_id)
                neu += 1
            self._publish_state(device_id, daten)
        if neu:
            events.add("event.mqtt_discovery", "debug",
                       count=neu * (len(SENSORS) + 1))

    def republish_all(self, zustaende) -> None:
        """Nach einem Sprachwechsel: Discovery mit neuen Namen erneuern."""
        self._announced.clear()
        self._bridge_announced = False
        self.publish_bridge()
        self.publish_devices(zustaende)

    def publish_bridge(self) -> None:
        """Globale Steuerung als eigenes Gerät in Home Assistant.

        Ein Schalter für die Verfolgung und eine Schaltfläche, die alle
        Geräte sofort orten lässt - das Gegenstück zu den Diensten, die
        eine Integration anbieten könnte, ein Add-on aber nicht.
        """
        if not self.client or not self.connected:
            return
        lang = store.get("sprache", default="de")
        praefix = store.get("entity_praefix", default="standort")
        basis = f"{praefix}/bridge"

        block = {
            "identifiers": [f"{praefix}_bridge"],
            "name": translator.t("app.title", lang),
            "manufacturer": "Standort-Manager",
            "model": translator.t("control.title", lang),
        }

        if not self._bridge_announced:
            self._publish_config("switch", f"{praefix}_tracking", {
                "unique_id": f"{praefix}_tracking",
                "object_id": f"{praefix}_tracking",
                "default_entity_id": f"switch.{praefix}_tracking",
                "name": translator.t("control.tracking", lang),
                "state_topic": f"{basis}/tracking",
                "command_topic": f"{basis}/tracking/set",
                "payload_on": "ON", "payload_off": "OFF",
                "availability_topic": self._availability_topic(),
                "icon": "mdi:radar",
                "device": block,
            })
            self._publish_config("button", f"{praefix}_locate_all", {
                "unique_id": f"{praefix}_locate_all",
                "object_id": f"{praefix}_locate_all",
                "default_entity_id": f"button.{praefix}_locate_all",
                "name": translator.t("control.locate_all", lang),
                "command_topic": f"{basis}/locate_all/set",
                "availability_topic": self._availability_topic(),
                "icon": "mdi:crosshairs-gps",
                "device": block,
            })
            self._bridge_announced = True

        from tracking import tracker
        self.client.publish(f"{basis}/tracking",
                            "OFF" if tracker.paused else "ON", retain=True)

    def _announce(self, device_id: str, daten: dict) -> None:
        lang = store.get("sprache", default="de")
        praefix = store.get("entity_praefix", default="standort")
        slug = entity_slug(device_id, daten.get("name"))
        basis = f"{praefix}/{slug}"
        geraetename = daten.get("name") or device_id

        # Kein via_device: Home Assistant legt ein Geraet, dessen via_device
        # es nicht kennt, stillschweigend nicht an - die Discovery liegt dann
        # zwar auf dem Broker, aber es entsteht keine einzige Entitaet, und
        # im Kernprotokoll steht nicht einmal eine Warnung.
        device_block = {
            "identifiers": [f"{praefix}_{slug}"],
            "name": geraetename,
            "manufacturer": "Apple",
            "model": daten.get("model") or "iPhone",
        }

        tracker_config = {
            "unique_id": f"{praefix}_{slug}_tracker",
            # object_id allein genügt nicht: neuere Home-Assistant-Versionen
            # bilden die Entitäts-ID sonst aus Geräte- plus Entitätsname und
            # ignorieren den Wunsch. default_entity_id ist das Feld, das
            # wirklich greift - object_id bleibt für ältere Versionen stehen.
            "object_id": f"{praefix}_{slug}",
            "default_entity_id": f"device_tracker.{praefix}_{slug}",
            "name": translator.t("sensor.location", lang),
            "state_topic": f"{basis}/state",
            "json_attributes_topic": f"{basis}/attributes",
            "availability_topic": self._availability_topic(),
            "source_type": "gps",
            "device": device_block,
            "icon": "mdi:account-arrow-right",
        }
        self._publish_config("device_tracker", f"{praefix}_{slug}", tracker_config)

        for key, label, device_class, unit, icon, kategorie in SENSORS:
            config = {
                "unique_id": f"{praefix}_{slug}_{key}",
                "object_id": f"{praefix}_{slug}_{key}",
                "default_entity_id": f"sensor.{praefix}_{slug}_{key}",
                "name": translator.t(label, lang),
                "state_topic": f"{basis}/{key}",
                "availability_topic": self._availability_topic(),
                "device": device_block,
            }
            if device_class:
                config["device_class"] = device_class
            if unit:
                config["unit_of_measurement"] = unit
            if icon:
                config["icon"] = icon
            if kategorie:
                config["entity_category"] = kategorie
            if device_class in ("distance", "speed", "duration", "battery"):
                config["state_class"] = "measurement"
            self._publish_config("sensor", f"{praefix}_{slug}_{key}", config)

        # Sensoren der zweiten Bezugszone nur anlegen, wenn eine gewählt ist -
        # sonst stünden für jedes Gerät drei ewig leere Entitäten herum.
        referenz = (daten.get("reference") or {}).get("zone_name")
        if referenz:
            for key, label, device_class, unit, icon in REFERENCE_SENSORS:
                config = {
                    "unique_id": f"{praefix}_{slug}_{key}",
                    "object_id": f"{praefix}_{slug}_{key}",
                    "default_entity_id": f"sensor.{praefix}_{slug}_{key}",
                    "name": translator.t(label, lang, zone=referenz),
                    "state_topic": f"{basis}/{key}",
                    "availability_topic": self._availability_topic(),
                    "device": device_block,
                    "icon": icon,
                }
                if device_class:
                    config["device_class"] = device_class
                if unit:
                    config["unit_of_measurement"] = unit
                    config["state_class"] = "measurement"
                self._publish_config("sensor", f"{praefix}_{slug}_{key}", config)

        versatz = store.device(device_id).get("timezone_offset") or 0
        if versatz:
            key, label, device_class, unit, icon = TIMEZONE_SENSOR
            self._publish_config("sensor", f"{praefix}_{slug}_{key}", {
                "unique_id": f"{praefix}_{slug}_{key}",
                "object_id": f"{praefix}_{slug}_{key}",
                "default_entity_id": f"sensor.{praefix}_{slug}_{key}",
                "name": translator.t(label, lang),
                "state_topic": f"{basis}/{key}",
                "availability_topic": self._availability_topic(),
                "icon": icon,
                "device": device_block,
            })

        for key, label, icon in BUTTONS:
            self._publish_config("button", f"{praefix}_{slug}_{key}", {
                "unique_id": f"{praefix}_{slug}_{key}",
                "object_id": f"{praefix}_{slug}_{key}",
                "default_entity_id": f"button.{praefix}_{slug}_{key}",
                "name": translator.t(label, lang),
                "command_topic": f"{basis}/{key}/set",
                "availability_topic": self._availability_topic(),
                "icon": icon,
                "device": device_block,
            })

        # Der Nachrichtentext gehört in eine eigene Entität, damit er aus
        # einer Automation heraus gesetzt werden kann, bevor die
        # Schaltfläche ihn verschickt.
        self._publish_config("text", f"{praefix}_{slug}_message", {
            "unique_id": f"{praefix}_{slug}_message",
            "object_id": f"{praefix}_{slug}_message",
            "default_entity_id": f"text.{praefix}_{slug}_message",
            "name": translator.t("control.message_text", lang),
            "state_topic": f"{basis}/message",
            "command_topic": f"{basis}/message/set",
            "availability_topic": self._availability_topic(),
            "max": 200,
            "icon": "mdi:message-text",
            "entity_category": "config",
            "device": device_block,
        })

    def _publish_config(self, komponente: str, object_id: str, config: dict) -> None:
        topic = f"{DISCOVERY_PREFIX}/{komponente}/{object_id}/config"
        self.client.publish(topic, json.dumps(config, ensure_ascii=False), retain=True)

    def _publish_state(self, device_id: str, daten: dict) -> None:
        lang = store.get("sprache", default="de")
        imperial = store.get("einheiten", default="metrisch") == "imperial"
        praefix = store.get("entity_praefix", default="standort")
        basis = f"{praefix}/{entity_slug(device_id, daten.get('name'))}"

        # device_tracker erwartet 'home' oder 'not_home' beziehungsweise
        # einen Zonennamen – das ist HA-Protokoll, nicht Anzeigetext.
        zone = daten.get("zone")
        if zone == "zone.home":
            tracker_state = "home"
        elif zone:
            tracker_state = daten.get("zone_name") or zone.replace("zone.", "")
        else:
            tracker_state = "not_home"
        self.client.publish(f"{basis}/state", tracker_state, retain=True)

        loc = daten.get("location") or {}
        attrs = {
            "source_type": "gps",
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "gps_accuracy": loc.get("accuracy"),
            "battery_level": daten.get("battery"),
            "attribution": "Standort-Manager",
            # Der Zustand als Schlüssel *und* übersetzt: Automationen
            # sollen gegen den Schlüssel prüfen, Karten den Text zeigen.
            "state_key": daten.get("state"),
            "state_text": translator.t(f"state.{daten.get('state','unknown')}", lang),
            # Auch die Fahrtrichtung sprachneutral mitliefern - Automationen
            # pruefen gegen 'towards', nicht gegen "Richtung Zuhause".
            "heading_key": daten.get("heading"),
            "source": daten.get("source"),
        }
        self.client.publish(
            f"{basis}/attributes",
            json.dumps({k: v for k, v in attrs.items() if v is not None},
                       ensure_ascii=False),
            retain=True,
        )

        werte = {}

        distanz = daten.get("distance_m")
        if distanz is not None:
            werte["distance"] = round(distanz / 1609.344, 2) if imperial \
                else round(distanz / 1000, 2)

        travel = daten.get("travel") or {}
        if travel.get("duration_seconds") is not None:
            werte["travel_time"] = round(travel["duration_seconds"] / 60)
        if travel.get("arrival_timestamp"):
            werte["arrival_time"] = _iso(travel["arrival_timestamp"])

        if daten.get("battery") is not None:
            werte["battery"] = daten["battery"]

        adresse = daten.get("address") or {}
        if adresse.get("display"):
            # MQTT-Sensoren sind bei 255 Zeichen am Ende.
            werte["address"] = adresse["display"][:250]

        werte["zone"] = (
            daten.get("zone_name")
            or translator.t("zone.away", lang)
        )

        if daten.get("last_seen"):
            werte["last_seen"] = _iso(daten["last_seen"])
        if loc.get("accuracy") is not None:
            werte["accuracy"] = round(loc["accuracy"])
        if daten.get("speed_ms") is not None:
            werte["speed"] = round(daten["speed_ms"] * (2.23694 if imperial else 3.6), 1)
        if daten.get("direction"):
            werte["direction"] = daten["direction"]
        if daten.get("interval"):
            werte["interval"] = daten["interval"]
        if daten.get("next_poll"):
            werte["next_update"] = _iso(daten["next_poll"])
        if daten.get("moved_distance") is not None:
            werte["moved_distance"] = daten["moved_distance"]
        if daten.get("zone_distance") is not None:
            werte["zone_distance"] = daten["zone_distance"]

        # Fahrtrichtung als fertiger Satz, weil sie den Zonennamen braucht.
        # Der Schlüssel steht daneben in den Attributen des Trackers.
        heading = daten.get("heading") or "unknown"
        heimzone = translator.t("zone.home", lang)
        werte["direction_home"] = translator.t(
            f"heading.{heading}", lang, zone=heimzone)

        akku_zustand = (daten.get("battery_status") or "").lower()
        werte["battery_state"] = translator.t(
            {"charging": "battery.charging",
             "notcharging": "battery.not_charging",
             "charged": "battery.full"}.get(akku_zustand, "battery.unknown"),
            lang)

        versatz = store.device(device_id).get("timezone_offset") or 0
        if versatz:
            from datetime import datetime, timedelta, timezone as tz
            ortszeit = datetime.now(tz.utc) + timedelta(hours=versatz)
            zeitformat = "%I:%M %p" if store.get("zeitformat", default="24h") == "12h" \
                else "%H:%M"
            werte["local_time"] = ortszeit.strftime(zeitformat)

        referenz = daten.get("reference") or {}
        if referenz.get("distance_m") is not None:
            werte["ref_distance"] = (
                round(referenz["distance_m"] / 1609.344, 2) if imperial
                else round(referenz["distance_m"] / 1000, 2)
            )
        ref_travel = (referenz.get("travel") or {})
        if ref_travel.get("duration_seconds") is not None:
            werte["ref_travel_time"] = round(ref_travel["duration_seconds"] / 60)
        if ref_travel.get("arrival_timestamp"):
            werte["ref_arrival_time"] = _iso(ref_travel["arrival_timestamp"])

        for key, wert in werte.items():
            self.client.publish(f"{basis}/{key}", str(wert), retain=True)

    def remove_device(self, device_id: str) -> None:
        """Entitäten eines Geräts bei Home Assistant abmelden."""
        if not self.client or not self.connected:
            return
        praefix = store.get("entity_praefix", default="standort")
        slug = entity_slug(device_id, None)
        self._publish_config_raw("device_tracker", f"{praefix}_{slug}")
        for key, *_ in SENSORS:
            self._publish_config_raw("sensor", f"{praefix}_{slug}_{key}")
        for key, *_ in REFERENCE_SENSORS:
            self._publish_config_raw("sensor", f"{praefix}_{slug}_{key}")
        self._publish_config_raw("sensor", f"{praefix}_{slug}_{TIMEZONE_SENSOR[0]}")
        for key, *_ in BUTTONS:
            self._publish_config_raw("button", f"{praefix}_{slug}_{key}")
        self._publish_config_raw("text", f"{praefix}_{slug}_message")
        self._announced.discard(device_id)

    def _publish_config_raw(self, komponente: str, object_id: str) -> None:
        # Leere Nachricht auf das Config-Topic löscht die Entität.
        self.client.publish(
            f"{DISCOVERY_PREFIX}/{komponente}/{object_id}/config", "", retain=True
        )

    def _availability_topic(self) -> str:
        praefix = store.get("entity_praefix", default="standort")
        return f"{praefix}/bridge/availability"

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "host": self.host,
            "port": self.port,
            "devices": len(self._announced),
        }


def entity_slug(device_id: str, name: str | None) -> str:
    """Kurzform für Entitäts-IDs und MQTT-Topics.

    Apple vergibt als Geräte-ID einen langen Base64-Block. Daraus gebaute
    Entitäten heißen `standort_aepuztvyafb6hykyy_irubg6y797hogwjury_cfz` –
    in einer Automation unbrauchbar. Der Name des Geräts ergibt eine
    lesbare ID.

    Damit das Umbenennen eines Geräts später keine Entitäts-ID zerreißt,
    wird die einmal vergebene Kurzform gespeichert und nie wieder
    geändert – genauso, wie Home Assistant es selbst hält.
    """
    gespeichert = store.get("entity_slugs", device_id)
    if gespeichert:
        return gespeichert
    # Ohne brauchbaren Namen nur eine Notform liefern und NICHT festschreiben,
    # sonst zementiert der erste Aufruf eine unlesbare ID für immer.
    if not name or name == device_id:
        return _slug(device_id)
    slug = _slug(name)
    # Kollision vermeiden: zwei Geräte desselben Namens bekämen sonst
    # dieselben Entitäten und würden sich gegenseitig überschreiben.
    vergeben = set((store.get("entity_slugs", default={}) or {}).values())
    if slug in vergeben:
        n = 2
        while f"{slug}_{n}" in vergeben:
            n += 1
        slug = f"{slug}_{n}"
    store.set("entity_slugs", device_id, slug)
    return slug


def _slug(text: str) -> str:
    """Entitätstaugliche Kurzform aus beliebigem Text."""
    out = []
    for zeichen in str(text).lower():
        if zeichen.isalnum() and zeichen.isascii():
            out.append(zeichen)
        elif out and out[-1] != "_":
            out.append("_")
    return "".join(out).strip("_")[:40] or "geraet"


def _iso(timestamp: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


publisher = MqttPublisher()
