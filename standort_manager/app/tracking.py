"""Die eigentliche Verfolgungslogik.

Aufgaben: aus Rohkoordinaten eine Zone machen, entscheiden wie oft das
nächste Mal gefragt wird, und zwischen iCloud und Companion-App die jeweils
bessere Quelle wählen.

Zum Nachdenken beim Lesen: Nichts hier erzeugt Text. Zustände sind
Schlüssel wie 'home' oder 'traveling', die erst beim Anzeigen oder
Veröffentlichen übersetzt werden.
"""

import logging
import math
import threading
import time

import geocode
import ha
import route
from fahrten import fahrtenbuch
from apple import account
from events import events
from store import store

log = logging.getLogger("standort.tracking")

ERDRADIUS_M = 6371000.0

# Ab dieser Geschwindigkeit gilt ein Gerät als fahrend und nicht als
# gehend – beeinflusst nur die Wahl des Abfragetakts.
FAHREND_MS = 5.0

# Unterhalb dieser Strecke zwischen zwei Messungen gilt ein Gerät als
# stehend. GPS rauscht, deshalb nicht null.
STILLSTAND_M = 60.0


class DeviceState:
    """Was die App über ein Gerät zwischen zwei Abfragen behält."""

    def __init__(self, device_id: str):
        self.id = device_id
        self.name = device_id
        self.model = None
        self.device_class = None
        self.battery = None
        self.battery_status = None
        self.location = None
        self.source = None
        self.zone = None
        self.zone_name = None
        self.state = "unknown"
        self.distance_m = None
        # Richtung relativ zur Bezugszone: towards / away / stationary.
        # Die Himmelsrichtung in self.direction beantwortet eine andere
        # Frage - für Automationen zählt "kommt näher", nicht "fährt nach SW".
        self.heading = "unknown"
        self.moved_distance = None
        self.zone_distance = None
        self.zone_nearest = None
        self.address = None
        self.travel = None
        # Zweite Bezugszone: dieselben Werte noch einmal für einen Ort,
        # der nicht das Zuhause ist - etwa die Schule.
        self.reference = None
        self.speed_ms = None
        self.direction = None
        self.last_seen = None
        self.last_poll = None
        self.next_poll = 0.0
        self.interval = None
        self.stationary_since = None
        self.offline = False
        # Für die Durchfahrtsprüfung: eine Zone gilt erst als betreten,
        # wenn sie lange genug gehalten wurde.
        self._pending_zone = None
        self._pending_since = None
        self._last_fix = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "device_class": self.device_class,
            "battery": self.battery,
            "battery_status": self.battery_status,
            "location": self.location,
            "source": self.source,
            "zone": self.zone,
            "zone_name": self.zone_name,
            "state": self.state,
            "distance_m": self.distance_m,
            "heading": self.heading,
            "moved_distance": self.moved_distance,
            "zone_distance": self.zone_distance,
            "zone_nearest": self.zone_nearest,
            "address": self.address,
            "travel": self.travel,
            "reference": self.reference,
            "speed_ms": self.speed_ms,
            "direction": self.direction,
            "last_seen": self.last_seen,
            "last_poll": self.last_poll,
            "next_poll": self.next_poll,
            "interval": self.interval,
            "offline": self.offline,
        }


class Tracker:
    """Hält den Zustand aller Geräte und den Abfrage-Thread."""

    def __init__(self):
        self._lock = threading.RLock()
        self.devices: dict[str, DeviceState] = {}
        self.zones: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._publisher = None
        self.last_cycle = None
        # Angehalten heißt: keine Abfragen bei Apple. Die Entitäten behalten
        # ihren letzten Wert, statt auf "unbekannt" zu fallen.
        self.paused = False

    def set_publisher(self, publisher) -> None:
        """MQTT-Veröffentlichung anmelden (optional)."""
        self._publisher = publisher

    # ----------------------------------------------------------------- Lauf

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.cycle()
            except Exception as err:  # noqa: BLE001 – der Thread muss leben
                log.exception("Fehler im Abfragezyklus")
                events.add("event.poll_failed", "error", reason=str(err)[:200])
            # Feiner Takt, damit ein fällig gewordenes Gerät nicht bis zum
            # nächsten groben Intervall warten muss.
            self._stop.wait(15)

    def set_paused(self, angehalten: bool) -> None:
        if self.paused == angehalten:
            return
        self.paused = angehalten
        events.add("event.tracking_paused" if angehalten
                   else "event.tracking_resumed", "info")
        if self._publisher:
            self._publisher.publish_bridge()
        if not angehalten:
            self.poll_now()

    def cycle(self) -> None:
        """Ein Durchlauf: prüfen, ob etwas fällig ist, und wenn ja abfragen."""
        if self.paused:
            return
        jetzt = time.time()
        self.zones = ha.zones()

        # Die Companion-App kostet nichts bei Apple, also immer mitlesen.
        self._read_companion(jetzt)

        faellig = self._due(jetzt)
        if not faellig:
            return

        geraete = account.fetch(locate=True)
        if not geraete:
            return
        self.last_cycle = jetzt

        aenderungen = []
        for roh in geraete:
            zustand = self._apply_icloud(roh, jetzt)
            if zustand:
                aenderungen.append(zustand)

        for zustand in aenderungen:
            self._enrich(zustand)
            self._schedule(zustand, jetzt)

        if self._publisher and aenderungen:
            self._publisher.publish_devices(aenderungen)

    def _due(self, jetzt: float) -> bool:
        """Ist mindestens ein verfolgtes Gerät zur Abfrage fällig?

        Apple liefert bei einer Abfrage ohnehin alle Geräte auf einmal –
        also lohnt kein Feilschen um einzelne, sobald eines dran ist.
        """
        with self._lock:
            verfolgt = [
                d for did, d in self.devices.items()
                if store.device(did)["modus"] == "track"
            ]
        if not verfolgt:
            # Noch nichts eingerichtet: trotzdem abfragen, damit die
            # Geräteliste in der Oberfläche gefüllt wird.
            return not self.devices
        return any(d.next_poll <= jetzt for d in verfolgt)

    # -------------------------------------------------------------- Quellen

    def _apply_icloud(self, roh: dict, jetzt: float) -> DeviceState | None:
        device_id = roh["id"]
        with self._lock:
            zustand = self.devices.setdefault(device_id, DeviceState(device_id))

        einstellung = store.device(device_id)
        # Reihenfolge: eigener Name aus der Oberfläche, sonst der Name vom
        # Gerät selbst, und erst als letzte Rettung die Modellbezeichnung.
        zustand.name = (
            einstellung["name"] or roh.get("name") or roh.get("model_name")
        )
        # Als Modell die lesbare Bezeichnung zeigen, nicht 'iPhone16-1-1-0'.
        zustand.model = roh.get("model_name") or roh.get("model")
        zustand.device_class = roh.get("device_class")
        vorheriger_akku = zustand.battery
        zustand.battery = roh.get("battery")
        zustand.battery_status = roh.get("battery_status")
        zustand.last_poll = jetzt

        if einstellung["modus"] == "inactive":
            return None

        # Nur an der Flanke melden – sonst steht die Warnung bei jeder
        # Abfrage erneut im Protokoll, bis jemand ladet.
        #
        # Und niemals bei 0 %: AirPods und Uhren, die gerade im Etui oder
        # am Ladegerät liegen, melden 0 statt gar nichts. Eine Warnung
        # daraus wäre reines Rauschen.
        if (
            zustand.battery
            and zustand.battery <= 20
            and (not vorheriger_akku or vorheriger_akku > 20)
        ):
            events.add("event.device_battery_low", "warning",
                       device_id=device_id, device=zustand.name,
                       percent=zustand.battery)

        loc = roh.get("location")
        # Nur bei "Verfolgen" melden: ein beobachtetes iPad, das im Schrank
        # liegt, ist kein Ereignis - es füllt nur das Protokoll.
        meldenswert = einstellung["modus"] == "track"

        if not loc:
            if not zustand.offline:
                zustand.offline = True
                if meldenswert:
                    events.add("event.device_offline", "warning",
                               device_id=device_id, device=zustand.name)
            return zustand

        if zustand.offline:
            zustand.offline = False
            if meldenswert:
                events.add("event.device_back_online", "info",
                           device_id=device_id, device=zustand.name)

        schwelle = store.get("genauigkeitsschwelle", default=100)
        if loc.get("accuracy") and loc["accuracy"] > schwelle and zustand.location:
            # Ungenaue Messung verwerfen – aber nur, wenn schon etwas
            # Besseres dasteht. Sonst bliebe ein Gerät für immer leer.
            return zustand

        self._update_location(zustand, loc, "icloud", jetzt)
        return zustand

    def _read_companion(self, jetzt: float) -> None:
        """Tracker der Companion-App mitlesen, wo eingerichtet.

        Die App meldet Zonenwechsel in Sekunden statt Minuten. Sie gewinnt
        deshalb immer, wenn ihre Messung frischer ist als die von iCloud.
        """
        for device_id, einstellung in (store.get("geraete", default={}) or {}).items():
            entity = einstellung.get("companion_entity")
            if not entity or einstellung.get("modus") != "track":
                continue
            loc = ha.tracker_location(entity)
            if not loc or not loc.get("timestamp"):
                continue

            with self._lock:
                zustand = self.devices.setdefault(device_id, DeviceState(device_id))

            aktuell = (zustand.location or {}).get("timestamp") or 0
            if loc["timestamp"] <= aktuell:
                continue

            # Solange iCloud das Gerät noch nie geliefert hat, steht in
            # zustand.name die Apple-Geräte-ID. Würde jetzt veröffentlicht,
            # bekäme die Entität eine ID aus diesem Base64-Block - und die
            # bleibt, weil Kurzformen absichtlich nie wieder wechseln.
            if zustand.name == device_id:
                continue

            # Der Wechsel zwischen iCloud und Companion-App ist der
            # Normalfall und passierte 122-mal an einem Tag. Als Ereignis
            # verdrängte er im Ringpuffer genau die Meldungen, die zählen.
            # Welche Quelle gerade gilt, steht im Gerätezustand und in den
            # MQTT-Attributen.
            if loc.get("battery") is not None:
                zustand.battery = loc["battery"]
            self._update_location(zustand, loc, "companion", jetzt)
            self._enrich(zustand)
            if self._publisher:
                self._publisher.publish_devices([zustand])

    # ------------------------------------------------------------- Auswertung

    def _update_location(self, zustand: DeviceState, loc: dict,
                         quelle: str, jetzt: float) -> None:
        vorher = zustand.location
        vorherige_zone = zustand.zone

        zustand.source = quelle
        zustand.location = loc
        zustand.last_seen = loc.get("timestamp") or jetzt

        # Tempo und Richtung aus zwei Messungen ableiten. Apple liefert
        # beides nicht mit, die Companion-App nur manchmal.
        if vorher and vorher.get("timestamp") and loc.get("timestamp"):
            dt = loc["timestamp"] - vorher["timestamp"]
            strecke = distance_m(
                vorher["latitude"], vorher["longitude"],
                loc["latitude"], loc["longitude"],
            )
            zustand.moved_distance = round(strecke)
            if dt > 0:
                zustand.speed_ms = round(strecke / dt, 1)
            zustand.direction = bearing(
                vorher["latitude"], vorher["longitude"],
                loc["latitude"], loc["longitude"],
            ) if strecke > STILLSTAND_M else zustand.direction

            if strecke < STILLSTAND_M:
                zustand.stationary_since = zustand.stationary_since or jetzt
            else:
                if zustand.stationary_since:
                    events.add("event.device_stationary", "debug",
                               device_id=zustand.id, device=zustand.name,
                               duration=_dauer(jetzt - zustand.stationary_since))
                zustand.stationary_since = None

        zone = self._zone_for(loc["latitude"], loc["longitude"])
        zone_id = zone["entity_id"] if zone else None

        # Durchfahrtsprüfung: ein Zonen*eintritt* muss sich halten, damit
        # kurzes Streifen nicht als Ankunft zählt.
        #
        # Sie greift bewusst nur, wenn dicht genug gemessen wird. Bei
        # iCloud-Takten von Minuten ist eine halbminütige Durchfahrt
        # ohnehin nicht erkennbar; dort würde das Warten die Ankunft nur
        # um eine ganze Abfrage verzögern. Schnelle Companion-Meldungen
        # dagegen laufen wirklich durch die Prüfung.
        haltezeit = store.get("durchfahrt_sekunden", default=30)
        messabstand = jetzt - (zustand._last_fix or 0)
        zustand._last_fix = jetzt

        if zone_id != zustand.zone:
            sofort = (
                haltezeit <= 0
                or zustand.zone is None   # erste Zuordnung überhaupt
                or zone_id is None        # Verlassen wird nie verzögert
                or messabstand >= haltezeit
            )
            if not sofort:
                if zustand._pending_zone != zone_id:
                    zustand._pending_zone = zone_id
                    zustand._pending_since = jetzt
                sofort = jetzt - (zustand._pending_since or jetzt) >= haltezeit

            if sofort:
                zustand.zone = zone_id
                zustand.zone_name = zone["name"] if zone else None
                zustand._pending_zone = None
                zustand._pending_since = None
                self._zone_event(zustand, vorherige_zone)
        else:
            zustand._pending_zone = None
            zustand._pending_since = None

        vorheriger_zustand = zustand.state
        vorherige_entfernung = zustand.distance_m
        zustand.state = self._state_for(zustand)
        zustand.distance_m = self._home_distance(loc)
        zustand.heading = self._heading(vorherige_entfernung, zustand.distance_m)
        zustand.zone_nearest, zustand.zone_distance = self._nearest_zone(loc)

        if zustand.state == "traveling" and vorheriger_zustand != "traveling":
            heim = next((z for z in self.zones if z["entity_id"] == "zone.home"), None)
            events.add("event.device_moving", "info", device_id=zustand.id,
                       device=zustand.name,
                       distance=_strecke(zustand.distance_m),
                       zone=heim["name"] if heim else "zone.home")

    def _zone_event(self, zustand: DeviceState, vorher: str | None) -> None:
        if zustand.zone:
            # Angekommen: aus geschätzter und tatsächlicher Dauer lernen.
            gelernt = fahrtenbuch.fahrt_endet(zustand.id, zustand.zone)
            if gelernt:
                log.info("%s: %s geschätzt, %s tatsächlich (Faktor %s)",
                         zustand.name, _dauer(gelernt["geschaetzt"]),
                         _dauer(gelernt["tatsaechlich"]), gelernt["faktor"])
        if vorher and vorher != zustand.zone:
            events.add("event.device_left", "info", device_id=zustand.id,
                       device=zustand.name, zone=_zone_label(vorher, self.zones))
        if zustand.zone:
            events.add("event.device_arrived", "info", device_id=zustand.id,
                       device=zustand.name,
                       zone=zustand.zone_name or zustand.zone)

    def _zone_for(self, lat: float, lon: float) -> dict | None:
        """Kleinste Zone, in der der Punkt liegt.

        Kleinste gewinnt, damit eine Zone innerhalb einer größeren – etwa
        das Büro im Ort – die gröbere schlägt.
        """
        abgeschaltet = store.get("zonen", default={}) or {}
        treffer = []
        for zone in self.zones:
            if zone.get("passive"):
                continue
            # Abgeschaltete Zonen zählen nicht als Aufenthaltsort. Die
            # Heimzone bleibt immer an - ohne sie hätte "zuhause" keine
            # Bedeutung mehr.
            if (zone["entity_id"] != "zone.home"
                    and abgeschaltet.get(zone["entity_id"], {}).get("aktiv") is False):
                continue
            d = distance_m(lat, lon, zone["latitude"], zone["longitude"])
            if d <= zone.get("radius", 100):
                treffer.append((zone.get("radius", 100), zone))
        if not treffer:
            return None
        return min(treffer, key=lambda x: x[0])[1]

    def _state_for(self, zustand: DeviceState) -> str:
        if zustand.offline:
            return "offline"
        if zustand.zone == "zone.home":
            return "home"
        if zustand.zone:
            return "in_zone"
        if zustand.speed_ms and zustand.speed_ms >= FAHREND_MS:
            return "traveling"
        if zustand.stationary_since:
            return "stationary"
        return "away"

    def _heading(self, vorher: float | None, jetzt: float | None) -> str:
        """Kommt das Gerät näher oder entfernt es sich?

        Schwelle, weil GPS rauscht: unter STILLSTAND_M wäre jede Messung
        mal die eine, mal die andere Richtung.
        """
        if vorher is None or jetzt is None:
            return "unknown"
        unterschied = vorher - jetzt
        if abs(unterschied) < STILLSTAND_M:
            return "stationary"
        return "towards" if unterschied > 0 else "away"

    def _nearest_zone(self, loc: dict) -> tuple[str | None, float | None]:
        """Nächstgelegene Zone und die Entfernung dorthin.

        Beantwortet die Frage "wie weit ist es bis zum nächsten bekannten
        Ort", auch wenn das Gerät in keiner Zone steht.
        """
        kandidaten = [
            (distance_m(loc["latitude"], loc["longitude"],
                        z["latitude"], z["longitude"]), z)
            for z in self.zones if not z.get("passive")
        ]
        if not kandidaten:
            return None, None
        entfernung, zone = min(kandidaten, key=lambda x: x[0])
        return zone["name"], round(entfernung)

    def _home_distance(self, loc: dict) -> float | None:
        heim = next((z for z in self.zones if z["entity_id"] == "zone.home"), None)
        if not heim:
            return None
        return round(
            distance_m(loc["latitude"], loc["longitude"],
                       heim["latitude"], heim["longitude"])
        )

    def _enrich(self, zustand: DeviceState) -> None:
        """Adresse und Fahrtdauer ergänzen – beides optional und teuer."""
        loc = zustand.location
        if not loc:
            return

        if store.get("geocode", "anbieter", default="nominatim") != "aus":
            sprache = store.get("sprache", default="de")
            vorherige_adresse = (zustand.address or {}).get("display")
            adresse = geocode.reverse(loc["latitude"], loc["longitude"], sprache)
            if adresse:
                zustand.address = adresse
                if adresse.get("display") != vorherige_adresse:
                    events.add("event.geocode_ok", "debug", device_id=zustand.id,
                               device=zustand.name)
            elif zustand.address is None:
                events.add("event.geocode_failed", "debug", device_id=zustand.id,
                           device=zustand.name, reason="error.geocode_unavailable")

        # Fahrtdauer nur, wenn das Gerät wirklich unterwegs ist. Zuhause
        # oder im Büro wäre die Zahl sinnlos und der Dienst belastet.
        if (
            store.get("route", "anbieter", default="osrm") != "aus"
            and zustand.state in ("traveling", "away")
            and zustand.distance_m
            and zustand.distance_m > 500
        ):
            heim = next((z for z in self.zones if z["entity_id"] == "zone.home"), None)
            if heim:
                ergebnis = route.travel_time(
                    loc["latitude"], loc["longitude"],
                    heim["latitude"], heim["longitude"],
                )
                if ergebnis:
                    ergebnis = _korrigiert(ergebnis, zustand.id, "zone.home")
                    fahrtenbuch.fahrt_beginnt(zustand.id, "zone.home",
                                              ergebnis["duration_seconds"])
                    if not zustand.travel:
                        events.add("event.route_ok", "debug", device_id=zustand.id,
                                   device=zustand.name, zone=heim["name"],
                                   duration=_dauer(ergebnis["duration_seconds"]))
                elif zustand.travel is None:
                    events.add("event.route_failed", "debug", device_id=zustand.id,
                               device=zustand.name,
                               reason="error.route_unavailable")
                zustand.travel = ergebnis
        elif zustand.state in ("home", "in_zone"):
            zustand.travel = None

        self._reference_zone(zustand, loc)

    def _reference_zone(self, zustand, loc: dict) -> None:
        """Entfernung und Fahrtdauer zu einer zweiten, frei gewählten Zone.

        Gedacht für Orte, die regelmäßig angesteuert werden, ohne das
        Zuhause zu sein - bei Schulkindern die jeweilige Schule.
        """
        zone_id = store.device(zustand.id).get("reference_zone")
        if not zone_id:
            zustand.reference = None
            return
        zone = next((z for z in self.zones if z["entity_id"] == zone_id), None)
        if not zone:
            zustand.reference = None
            return

        entfernung = round(distance_m(loc["latitude"], loc["longitude"],
                                      zone["latitude"], zone["longitude"]))
        eintrag = {
            "zone": zone_id,
            "zone_name": zone["name"],
            "distance_m": entfernung,
            "in_zone": entfernung <= zone.get("radius", 100),
            "travel": None,
        }
        # Fahrtdauer nur, wenn das Ziel überhaupt noch vor einem liegt.
        if (
            store.get("route", "anbieter", default="osrm") != "aus"
            and not eintrag["in_zone"]
            and entfernung > 500
            and zustand.state in ("traveling", "away", "stationary")
        ):
            roh = route.travel_time(
                loc["latitude"], loc["longitude"],
                zone["latitude"], zone["longitude"],
            )
            if roh:
                eintrag["travel"] = _korrigiert(roh, zustand.id, zone_id)
                fahrtenbuch.fahrt_beginnt(zustand.id, zone_id,
                                          eintrag["travel"]["duration_seconds"])
        zustand.reference = eintrag

    def _schedule(self, zustand: DeviceState, jetzt: float) -> None:
        """Nächste Abfrage festlegen."""
        intervalle = store.get("intervalle", default={}) or {}
        if zustand.offline:
            takt = intervalle.get("offline", 1200)
        elif zustand.state == "home":
            takt = intervalle.get("zuhause", 900)
        elif zustand.state == "in_zone":
            takt = intervalle.get("zuhause", 900)
        elif zustand.state == "traveling":
            takt = intervalle.get("unterwegs", 120)
            # Je näher am Ziel, desto häufiger – aber nie öfter als der
            # Takt für Zonennähe erlaubt.
            if zustand.distance_m and zustand.speed_ms and zustand.speed_ms > 1:
                rest = zustand.distance_m / zustand.speed_ms
                takt = max(intervalle.get("zonennaehe", 60), min(takt, rest / 4))
        elif zustand.state == "stationary":
            takt = intervalle.get("abwesend", 300)
        else:
            takt = intervalle.get("abwesend", 300)

        takt = int(max(30, takt))
        if zustand.interval != takt:
            events.add("event.interval_changed", "debug", device_id=zustand.id,
                       device=zustand.name, interval=f"{takt}s")
        zustand.interval = takt
        zustand.next_poll = jetzt + takt

    # ---------------------------------------------------------------- Zugriff

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [d.as_dict() for d in self.devices.values()]

    def get(self, device_id: str) -> DeviceState | None:
        with self._lock:
            return self.devices.get(device_id)

    def poll_now(self) -> None:
        """Alle Geräte sofort fällig stellen."""
        with self._lock:
            for zustand in self.devices.values():
                zustand.next_poll = 0.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine-Entfernung in Metern."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * ERDRADIUS_M * math.asin(math.sqrt(a))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Himmelsrichtung als Kürzel.

    Bewusst als Kürzel und nicht als Wort: 'NO' übersetzt die Oberfläche,
    ein ausgeschriebenes 'Nordost' im Zustand wäre wieder fest verdrahtet.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    grad = (math.degrees(math.atan2(y, x)) + 360) % 360
    richtungen = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return richtungen[round(grad / 45) % 8]


def _korrigiert(ergebnis: dict, geraet: str, ziel: str) -> dict:
    """Eine Routenschätzung um den gelernten Faktor anheben.

    Der Routendienst rechnet mit freier Straße. Was diese Strecke zu dieser
    Tageszeit wirklich kostet, weiß nur das Fahrtenbuch.
    """
    faktor = fahrtenbuch.faktor(geraet, ziel)
    if faktor == 1.0:
        return ergebnis
    dauer = ergebnis["duration_seconds"] * faktor
    return {
        **ergebnis,
        "duration_seconds": round(dauer),
        "arrival_timestamp": time.time() + dauer,
        "faktor": faktor,
    }


def _dauer(sekunden: float) -> str:
    """Dauer knapp ausdrücken.

    Bewusst ohne Wörter: die Einheit kommt aus dem Katalog, die Zahl aus
    der Rechnung. So bleibt auch diese Stelle sprachfrei.
    """
    minuten = round(sekunden / 60)
    if minuten < 60:
        return f"{minuten} min"
    stunden = minuten // 60
    rest = minuten % 60
    return f"{stunden} h {rest} min" if rest else f"{stunden} h"


def _strecke(meter: float | None) -> str:
    if meter is None:
        return "?"
    return f"{round(meter / 1000, 1)} km" if meter >= 1000 else f"{round(meter)} m"


def _zone_label(entity_id: str, zones: list) -> str:
    for zone in zones:
        if zone["entity_id"] == entity_id:
            return zone["name"]
    return entity_id


tracker = Tracker()
