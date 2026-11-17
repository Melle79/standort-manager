"""Anbindung an Apples Ortungsdienst über pyicloud.

Die Anmeldung läuft ausschließlich über die Oberfläche: Apple-ID, Passwort
und der Bestätigungscode werden vom Benutzer eingegeben, nie aus der
Add-on-Konfiguration gelesen. Die Sitzung landet als Cookie-Verzeichnis
unter /data und überlebt Neustarts, sodass der Code nur alle paar Wochen
nötig ist.

Alles hier ist synchron und wird vom Abfrage-Thread aufgerufen; der Lock
verhindert, dass die Oberfläche gleichzeitig eine Anmeldung anstößt.
"""

import logging
import re
import threading
import time

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailable,
)

import ha
from events import events
from i18n import translator
from store import SESSION_DIR, store

log = logging.getLogger("standort.apple")

# Apple antwortet im Fehlerfall gern mit einer ganzen HTML-Seite. Die steht
# dann im Protokoll und macht es unlesbar - also nur den Klartext behalten.
_HTML = re.compile(r"<[^>]*>|&[a-z]+;", re.S)
_LEERRAUM = re.compile(r"\s+")


def _klartext(fehler, grenze: int = 140) -> str:
    """Eine Fehlermeldung auf das Lesbare eindampfen."""
    text = _LEERRAUM.sub(" ", _HTML.sub(" ", str(fehler))).strip()
    # Was vor dem ersten HTML-Brocken stand, ist meist die eigentliche
    # Aussage - alles danach nur Seitengeruest.
    text = text.split("  ")[0].strip() or text
    # Fehlerseiten wiederholen ihre Aussage gern in Titel und Ueberschrift.
    haelfte = len(text) // 2
    if haelfte > 8 and text[:haelfte].strip() == text[haelfte:].strip():
        text = text[:haelfte].strip()
    return text[:grenze].rstrip(" ,;:") or "?"

# Apple meldet eine abgelaufene Sitzung nicht als eigenen Ausnahmetyp,
# sondern als gewöhnlichen API-Fehler mit 409 oder 401. Ohne diese Liste
# sähe das aus wie ein Abfragefehler - und die App drehte sich im Kreis,
# statt sich neu anzumelden.
SITZUNG_ABGELAUFEN = (
    "authentication required",
    "(409)",
    "(401)",
    "unauthorized",
    "session expired",
    # So antwortet Apple auch, wenn es eine Sitzung verwirft - nicht nur bei
    # einem falschen Passwort. Am 28.10.2026 stand das zwei Stunden lang
    # bei jeder Abfrage im Protokoll, ohne dass sich etwas tat.
    "invalid email/password",
)

# So oft darf eine Wiederanmeldung höchstens versucht werden, bevor die
# App aufgibt und nach einem Code fragt - sonst hämmert sie bei einem
# echten Passwortproblem gegen Apple.
WIEDERANMELDUNG_PAUSE = 300
# Lehnt Apple die Anmeldung ab, wird es mit wachsendem Abstand noch einige
# Male versucht. Danach nicht mehr: Bei einem wirklich falschen Passwort
# würde Dauerprobieren das Apple-Konto sperren.
WIEDERANMELDUNG_MAX_PAUSE = 3600
WIEDERANMELDUNG_MAX_VERSUCHE = 6
# Nach einer gescheiterten Abfrage nicht im 15-Sekunden-Takt weiterfragen.
FEHLERPAUSE_START = 60
FEHLERPAUSE_MAX = 900


# Zustände, die die Oberfläche unterscheidet.
STATE_LOGGED_OUT = "logged_out"
STATE_TWOFACTOR = "twofactor"
STATE_READY = "ready"
STATE_ERROR = "error"


class AppleAccount:
    """Ein einzelnes Apple-Konto."""

    def __init__(self, username: str | None = None):
        self._lock = threading.RLock()
        self._api: PyiCloudService | None = None
        self.state = STATE_LOGGED_OUT
        self.error_key: str | None = None
        self.username: str | None = username
        # Zwischen Anmeldung und Bestätigungscode gemerkt: beim 2FA-Weg
        # ist die Anmeldung erst nach dem Code abgeschlossen, gespeichert
        # werden darf also auch erst dann.
        self._pending_password: str | None = None
        self._pending_store: bool = True
        # Wann zuletzt selbst neu angemeldet wurde - gegen Dauerversuche.
        self._letzte_wiederanmeldung = 0.0
        self._wiederanmeldungen = 0
        # Damit der Hinweis nicht bei jedem Zyklus erneut kommt.
        self._stillstand_gemeldet = False
        self._fehlerpause = 0
        # Apple bremst bei zu vielen Abfragen. Läuft eine Sperre, wird bis
        # zu diesem Zeitpunkt gar nicht erst gefragt.
        self.throttled_until = 0.0

    # ------------------------------------------------------------- Anmeldung

    @staticmethod
    def _session_dir(username: str):
        """Eigenes Cookie-Verzeichnis je Konto.

        Zwei Konten im selben Verzeichnis überschreiben sich gegenseitig die
        Sitzung - danach ist ständig eines von beiden abgemeldet.
        """
        sicher = "".join(c if c.isalnum() else "_" for c in username.lower())[:60]
        return SESSION_DIR / (sicher or "konto")

    def restore(self) -> None:
        """Beim Start versuchen, an die gespeicherte Sitzung anzuknüpfen."""
        username, password = store.credentials(self.username)
        if not username:
            return
        self.username = username
        self.login(username, password, store_password=password is not None,
                   from_restore=True)

    def login(self, username: str, password: str | None,
              store_password: bool = True, from_restore: bool = False) -> dict:
        """Anmelden. Gibt den neuen Zustand zurück.

        Ohne Passwort wird nur die gespeicherte Sitzung probiert – das ist
        der Normalfall nach einem Neustart.
        """
        with self._lock:
            sitzung = self._session_dir(username)
            sitzung.mkdir(parents=True, exist_ok=True)
            try:
                api = PyiCloudService(
                    apple_id=username,
                    password=password,
                    cookie_directory=str(sitzung),
                    with_family=True,
                )
            except PyiCloudFailedLoginException as err:
                self._api = None
                self.state = STATE_ERROR
                self.error_key = "error.bad_credentials"
                # Auch beim Wiederanknüpfen nach einem Neustart: Bis 0.6.2
                # stand hier "if not from_restore", und das Add-on blieb
                # 20 Tage still abgemeldet - ohne Protokolleintrag, ohne
                # Hinweis in Home Assistant.
                events.add("event.login_failed", "error", user=username,
                           reason=_klartext(err))
                if from_restore:
                    self._melde_stillstand("notify.login_failed")
                return self.status()
            except (PyiCloud2FARequiredException, PyiCloud2SARequiredException):
                # pyicloud legt das Objekt trotzdem an – für den Codeschritt
                # brauchen wir es, also nochmal ohne sofortige Anmeldung.
                api = PyiCloudService(
                    apple_id=username,
                    password=password,
                    cookie_directory=str(self._session_dir(username)),
                    with_family=True,
                    authenticate=False,
                )
                self._api = api
                self.username = username
                self._pending_password = password
                self._pending_store = store_password
                self.state = STATE_TWOFACTOR
                self.error_key = None
                events.add("event.twofactor_required", "warning")
                if from_restore:
                    self._melde_stillstand("notify.needs_code")
                return self.status()
            except PyiCloudAPIResponseException as err:
                self._api = None
                self.state = STATE_ERROR
                self.error_key = "error.apple_unavailable"
                events.add("event.login_failed", "error", user=username,
                           reason=_klartext(err))
                if from_restore:
                    self._melde_stillstand("notify.login_failed")
                return self.status()

            self._api = api
            self.username = username

            if api.requires_2fa or api.requires_2sa:
                self._pending_password = password
                self._pending_store = store_password
                self.state = STATE_TWOFACTOR
                self.error_key = None
                events.add("event.twofactor_required", "warning")
                # Nur melden, wenn die App von sich aus angemeldet hat.
                # Bei einer Anmeldung über die Oberfläche sitzt jemand
                # davor und sieht die Code-Abfrage ohnehin.
                if from_restore:
                    self._melde_stillstand("notify.needs_code")
                return self.status()

            self.state = STATE_READY
            self.error_key = None
            self._wiederanmeldungen = 0
            # Immer aufräumen, nicht nur nach eigener Meldung: Die
            # Reparaturmeldung überlebt einen Neustart, das Wissen um sie
            # im Add-on nicht.
            ha.meldung_zuruecknehmen()
            self._stillstand_gemeldet = False
            store.set_credentials(username, password if store_password else None)
            events.add("event.login_ok", "info", user=username)
            return self.status()

    def submit_2fa(self, code: str, trust: bool = True) -> dict:
        """Bestätigungscode einreichen."""
        with self._lock:
            if not self._api:
                self.error_key = "error.not_logged_in"
                return self.status()
            try:
                ok = self._api.validate_2fa_code(code)
            except PyiCloudAPIResponseException as err:
                self.error_key = "error.apple_unavailable"
                events.add("event.login_failed", "error",
                           user=self.username or "", reason=_klartext(err))
                return self.status()

            if not ok:
                self.error_key = "error.bad_code"
                return self.status()

            events.add("event.twofactor_ok", "info")
            if trust:
                try:
                    self._api.trust_session()
                except PyiCloudAPIResponseException as err:
                    # Nicht schlimm: ohne Vertrauen fragt Apple nur öfter.
                    log.warning("trust_session fehlgeschlagen: %s", err)

            # Erst jetzt ist die Anmeldung fertig – also erst jetzt
            # speichern. Ohne das wäre nach einem Neustart des Add-ons
            # niemand mehr angemeldet, weil restore() keine Kennung fände.
            if self.username:
                store.set_credentials(
                    self.username,
                    self._pending_password if self._pending_store else None,
                )
            self._pending_password = None

            self.state = STATE_READY
            self.error_key = None
            self._stillstand_gemeldet = False
            self._wiederanmeldungen = 0
            ha.meldung_zuruecknehmen()
            events.add("event.login_ok", "info", user=self.username or "")
            return self.status()

    def request_new_code(self) -> bool:
        with self._lock:
            if not self._api:
                return False
            try:
                return bool(self._api.request_2fa_code())
            except PyiCloudAPIResponseException:
                return False

    def logout(self, forget: bool = False) -> None:
        with self._lock:
            if self._api:
                try:
                    self._api.logout(clear_local_session=forget)
                except Exception as err:  # Abmelden darf nie hängenbleiben
                    log.warning("Abmelden fehlgeschlagen: %s", err)
            self._api = None
            self.state = STATE_LOGGED_OUT
            self.error_key = None
            if forget and self.username:
                store.clear_credentials(self.username)

    # --------------------------------------------------------------- Abfrage

    def fetch(self, locate: bool = True) -> list[dict]:
        """Geräte samt Standort holen.

        Wirft nicht – bei einem Fehler wird der Zustand gesetzt und eine
        leere Liste zurückgegeben, damit der Abfrage-Thread weiterläuft.
        """
        with self._lock:
            # Abgelehnte Anmeldung: nicht still liegen bleiben, sondern es in
            # wachsendem Abstand selbst wieder versuchen.
            if self.state == STATE_ERROR and self.username:
                self._sitzung_erneuern()
            if self.state != STATE_READY or not self._api:
                return []
            if time.time() < self.throttled_until:
                return []

            started = time.time()
            try:
                self._api.devices.refresh(locate=locate)
                geraete = [_normalize(d) for d in self._api.devices]
            except (PyiCloud2FARequiredException, PyiCloud2SARequiredException):
                events.add("event.session_expired", "warning")
                self._sitzung_erneuern()
                return []
            except PyiCloudServiceNotActivatedException as err:
                self.state = STATE_ERROR
                self.error_key = "error.apple_unavailable"
                events.add("event.poll_failed", "error", reason=_klartext(err))
                return []
            except PyiCloudAPIResponseException as err:
                text = str(err).lower()
                # 503 und 450 bedeuten in der Praxis: zu oft gefragt.
                if any(code in text for code in ("503", "450", "rate")):
                    self.throttled_until = time.time() + 600
                    events.add("event.poll_skipped", "warning",
                               reason="error.rate_limited")
                elif any(m in text for m in SITZUNG_ABGELAUFEN):
                    # Kein gewöhnlicher Abfragefehler: die Sitzung ist hin.
                    # Ohne diesen Zweig scheitert jede weitere Abfrage
                    # genauso, bis jemand von Hand eingreift.
                    events.add("event.session_expired", "warning")
                    self._sitzung_erneuern()
                else:
                    events.add("event.poll_failed", "error",
                               reason=_klartext(err))
                    self._pause_nach_fehler()
                return []
            except Exception as err:  # noqa: BLE001 – der Thread muss leben
                if any(m in str(err).lower() for m in SITZUNG_ABGELAUFEN):
                    events.add("event.session_expired", "warning")
                    self._sitzung_erneuern()
                else:
                    events.add("event.poll_failed", "error",
                               reason=_klartext(err))
                    self._pause_nach_fehler()
                return []

            self._fehlerpause = 0
            for eintrag in geraete:
                eintrag["account"] = self.username
            events.add("event.poll_ok", "debug", count=len(geraete),
                       seconds=round(time.time() - started, 1))
            return geraete

    def _sitzung_erneuern(self) -> bool:
        """Selbst neu anmelden, wenn die Sitzung abgelaufen ist.

        Bei einer vertrauenswürdigen Sitzung geht das mit dem gespeicherten
        Passwort ohne Bestätigungscode durch. Erst wenn Apple doch einen
        verlangt, muss der Benutzer ran - und erfährt das dann auch.
        """
        jetzt = time.time()
        pause = min(WIEDERANMELDUNG_PAUSE * 2 ** self._wiederanmeldungen,
                    WIEDERANMELDUNG_MAX_PAUSE)
        if jetzt - self._letzte_wiederanmeldung < pause:
            return False
        if self._wiederanmeldungen >= WIEDERANMELDUNG_MAX_VERSUCHE:
            return False
        self._letzte_wiederanmeldung = jetzt
        self._wiederanmeldungen += 1

        username, passwort = store.credentials(self.username)
        if not username or not passwort:
            # Ohne Passwort ist nichts zu machen; der Benutzer muss ran.
            self.state = STATE_TWOFACTOR
            self._melde_stillstand("notify.needs_code")
            return False

        events.add("event.session_refresh", "warning")
        ergebnis = self.login(username, passwort, store_password=True,
                              from_restore=True)

        if ergebnis["state"] == STATE_READY:
            events.add("event.session_refreshed", "info")
            ha.meldung_zuruecknehmen()
            return True

        if ergebnis["state"] == STATE_TWOFACTOR:
            self._melde_stillstand("notify.needs_code")
        else:
            events.add("event.session_refresh_failed", "error",
                       reason=ergebnis.get("error_key") or "error.generic")
            self._melde_stillstand("notify.login_failed")
        return False

    def _pause_nach_fehler(self) -> None:
        """Nach einer gescheiterten Abfrage mit wachsendem Abstand warten.

        Ohne Pause fragte das Add-on bei einem Dauerfehler alle 15 Sekunden
        erneut bei Apple an - am 28.10.2026 über 400-mal in zwei Stunden.
        """
        self._fehlerpause = min(max(self._fehlerpause * 2, FEHLERPAUSE_START),
                                FEHLERPAUSE_MAX)
        self.throttled_until = time.time() + self._fehlerpause

    def _melde_stillstand(self, text_key: str) -> None:
        """Den Benutzer aktiv darauf hinweisen, dass nichts mehr läuft.

        Höchstens einmal je Stillstand: Ohne die Sperre stünde nach jedem
        Abfragezyklus ein neuer Protokolleintrag da.
        """
        if self._stillstand_gemeldet:
            return
        self._stillstand_gemeldet = True
        if text_key == "notify.needs_code":
            events.add("event.needs_code", "error")
        lang = store.get("sprache", default="de")
        ha.melde(translator.t("notify.title", lang),
                 translator.t(text_key, lang))

    # --------------------------------------------------------------- Befehle

    def _device(self, device_id: str):
        """Das pyicloud-Objekt zu einer Geräte-ID finden."""
        if not self._api:
            return None
        for geraet in self._api.devices:
            inhalt = geraet.data if hasattr(geraet, "data") else {}
            if inhalt.get("id") == device_id or inhalt.get("baUUID") == device_id:
                return geraet
        return None

    def play_sound(self, device_id: str, betreff: str = "Standort-Manager") -> dict:
        """Das Gerät einen Ton abspielen lassen – auch im Lautlos-Modus."""
        return self._command(device_id, "play_sound",
                             lambda g: g.play_sound(subject=betreff))

    def display_message(self, device_id: str, nachricht: str,
                        betreff: str = "Standort-Manager",
                        ton: bool = True) -> dict:
        """Eine Nachricht auf dem Sperrbildschirm anzeigen."""
        return self._command(
            device_id, "display_message",
            lambda g: g.display_message(subject=betreff, message=nachricht,
                                        sounds=ton),
        )

    def lost_device(self, device_id: str, nummer: str, text: str) -> dict:
        """Verloren-Modus einschalten.

        Sperrt das iPhone und zeigt Nummer und Text an. Bewusst nur über
        die Oberfläche mit Rückfrage erreichbar und nicht als Schaltfläche
        in Home Assistant: ein Fehlgriff sperrt ein Familienhandy.
        """
        return self._command(
            device_id, "lost_device",
            lambda g: g.lost_device(number=nummer, text=text),
        )

    def _command(self, device_id: str, name: str, aktion) -> dict:
        """Gemeinsamer Rahmen: Gerät suchen, Aktion ausführen, melden."""
        with self._lock:
            if self.state != STATE_READY or not self._api:
                return {"ok": False, "error_key": "error.not_logged_in"}
            geraet = self._device(device_id)
            if not geraet:
                return {"ok": False, "error_key": "error.device_not_found"}
            try:
                aktion(geraet)
            except PyiCloudServiceUnavailable:
                # Ein Mac nimmt keinen Ton an, AirPods keine Nachricht.
                return {"ok": False, "error_key": "error.command_unavailable"}
            except PyiCloudAPIResponseException as err:
                return {"ok": False, "error_key": "error.apple_unavailable",
                        "detail": _klartext(err)}
            except Exception as err:  # noqa: BLE001
                return {"ok": False, "error_key": "error.generic",
                        "detail": _klartext(err)}
            return {"ok": True, "command": name}

    # ---------------------------------------------------------------- Status

    def status(self) -> dict:
        with self._lock:
            trusted = False
            valid_until = None
            if self._api and self.state == STATE_READY:
                try:
                    trusted = bool(self._api.is_trusted_session)
                except Exception:  # noqa: BLE001
                    trusted = False
            return {
                "state": self.state,
                "username": self.username,
                "error_key": self.error_key,
                "trusted_session": trusted,
                "session_valid_until": valid_until,
                "throttled": time.time() < self.throttled_until,
                "has_stored_password":
                    store.credentials(self.username)[1] is not None,
            }


def _normalize(device) -> dict:
    """Ein pyicloud-Gerät auf unser eigenes, schlankes Format bringen.

    Absicht: Der Rest der App kennt pyicloud nicht. Ändert Apple die
    Feldnamen, ist nur diese Funktion betroffen.
    """
    content = device.data if hasattr(device, "data") else {}
    location = device.location or {}

    battery = content.get("batteryLevel")
    if isinstance(battery, (int, float)):
        # Apple liefert 0..1, Home Assistant will Prozent.
        battery = round(battery * 100)
    else:
        battery = None

    # Apple trennt beides sauber, ich hatte es verwechselt:
    #   name              -> der Name, den der Besitzer vergeben hat
    #   deviceDisplayName -> die Modellbezeichnung, z.B. "iPhone 16 Pro Max"
    # Zwei iPhones desselben Modells sind über deviceDisplayName nicht
    # unterscheidbar, über name schon.
    return {
        "id": content.get("id") or content.get("baUUID") or device.name,
        "name": content.get("name") or device.name,
        "model_name": content.get("deviceDisplayName") or device.model_name,
        "model": content.get("deviceModel") or device.model,
        "raw_model": content.get("rawDeviceModel"),
        "device_class": content.get("deviceClass"),
        "battery": battery,
        "battery_status": content.get("batteryStatus"),
        "device_status": content.get("deviceStatus"),
        "low_power_mode": content.get("lowPowerMode"),
        "location": _location(location),
    }


def _location(loc: dict) -> dict | None:
    if not loc or loc.get("latitude") is None:
        return None
    timestamp = loc.get("timeStamp")
    # Apple liefert Millisekunden seit Epoche.
    if isinstance(timestamp, (int, float)) and timestamp > 1e11:
        timestamp = timestamp / 1000
    return {
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "accuracy": loc.get("horizontalAccuracy"),
        "timestamp": timestamp,
        "is_old": bool(loc.get("isOld")),
        "is_inaccurate": bool(loc.get("isInaccurate")),
        "position_type": loc.get("positionType"),
        "finished": bool(loc.get("locationFinished", True)),
    }


def _sitzung_migrieren(konten: list[str]) -> None:
    """Die Sitzung des früheren Einzelkontos ins Unterverzeichnis holen.

    Vorher lagen Cookies und Sitzungsdatei direkt in apple-session, jetzt
    bekommt jedes Konto ein eigenes Unterverzeichnis. Ohne diesen Umzug
    fände pyicloud die gültige Sitzung nicht mehr - und Apple verlangte
    ohne Not einen neuen Bestätigungscode.
    """
    if not konten or not SESSION_DIR.is_dir():
        return
    lose = [p for p in SESSION_DIR.iterdir() if p.is_file()]
    if not lose:
        return
    ziel = AppleAccount._session_dir(konten[0])
    try:
        ziel.mkdir(parents=True, exist_ok=True)
        for datei in lose:
            datei.replace(ziel / datei.name)
        log.info("Sitzung von %s nach %s übernommen", SESSION_DIR.name, ziel.name)
    except OSError as err:
        log.warning("Sitzung nicht umziehbar: %s", err)


class AccountManager:
    """Mehrere Apple-Konten unter einem Dach.

    Nach aussen verhaelt sich der Manager wie ein einzelnes Konto, solange
    nur eines eingerichtet ist - deshalb bleiben die alten Aufrufe heil.
    Sobald mehrere hinterlegt sind, fragt fetch() alle nacheinander ab und
    Befehle finden ihr Konto ueber die Geraete-ID.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.konten: dict[str, AppleAccount] = {}
        # device_id -> Apple-ID, damit ein Befehl das richtige Konto trifft.
        self._zuordnung: dict[str, str] = {}

    # ----------------------------------------------------------- Verwaltung

    def restore(self) -> None:
        _sitzung_migrieren(store.accounts())
        for username in store.accounts():
            konto = self.konten.setdefault(username, AppleAccount(username))
            konto.restore()

    def konto(self, username: str | None = None) -> AppleAccount:
        """Ein Konto holen oder anlegen. Ohne Angabe das erste."""
        with self._lock:
            if username is None:
                username = next(iter(self.konten), None)
            if username is None:
                return AppleAccount()
            return self.konten.setdefault(username, AppleAccount(username))

    def login(self, username: str, password: str | None,
              store_password: bool = True) -> dict:
        with self._lock:
            konto = self.konten.setdefault(username, AppleAccount(username))
        return konto.login(username, password, store_password)

    def submit_2fa(self, username: str | None, code: str,
                   trust: bool = True) -> dict:
        return self.konto(username).submit_2fa(code, trust)

    def request_new_code(self, username: str | None = None) -> bool:
        return self.konto(username).request_new_code()

    def logout(self, username: str | None = None, forget: bool = False) -> None:
        with self._lock:
            ziele = ([self.konten[username]] if username and username in self.konten
                     else list(self.konten.values()))
        for konto in ziele:
            konto.logout(forget=forget)
        if forget:
            with self._lock:
                for konto in ziele:
                    self.konten.pop(konto.username, None)
                    self._zuordnung = {
                        d: k for d, k in self._zuordnung.items()
                        if k != konto.username
                    }

    # -------------------------------------------------------------- Abfrage

    def fetch(self, locate: bool = True) -> list[dict]:
        """Alle bereiten Konten abfragen und die Geräte zusammenlegen."""
        with self._lock:
            konten = list(self.konten.values())
        geraete = []
        for konto in konten:
            for eintrag in konto.fetch(locate=locate):
                self._zuordnung[eintrag["id"]] = konto.username
                geraete.append(eintrag)
        return geraete

    # -------------------------------------------------------------- Befehle

    def _fuer_geraet(self, device_id: str) -> AppleAccount | None:
        username = self._zuordnung.get(device_id)
        if username and username in self.konten:
            return self.konten[username]
        # Noch keine Zuordnung: bei einem einzigen Konto ist sie eindeutig.
        with self._lock:
            if len(self.konten) == 1:
                return next(iter(self.konten.values()))
        return None

    def play_sound(self, device_id: str, betreff: str = "Standort-Manager") -> dict:
        konto = self._fuer_geraet(device_id)
        if not konto:
            return {"ok": False, "error_key": "error.device_not_found"}
        return konto.play_sound(device_id, betreff)

    def display_message(self, device_id: str, nachricht: str,
                        betreff: str = "Standort-Manager", ton: bool = True) -> dict:
        konto = self._fuer_geraet(device_id)
        if not konto:
            return {"ok": False, "error_key": "error.device_not_found"}
        return konto.display_message(device_id, nachricht, betreff, ton)

    def lost_device(self, device_id: str, nummer: str, text: str) -> dict:
        konto = self._fuer_geraet(device_id)
        if not konto:
            return {"ok": False, "error_key": "error.device_not_found"}
        return konto.lost_device(device_id, nummer, text)

    # --------------------------------------------------------------- Status

    @property
    def state(self) -> str:
        """Gesamtzustand: bereit, sobald mindestens ein Konto bereit ist."""
        with self._lock:
            zustaende = [k.state for k in self.konten.values()]
        if not zustaende:
            return STATE_LOGGED_OUT
        if STATE_READY in zustaende:
            return STATE_READY
        if STATE_TWOFACTOR in zustaende:
            return STATE_TWOFACTOR
        return zustaende[0]

    @property
    def username(self) -> str | None:
        with self._lock:
            return next(iter(self.konten), None)

    def status(self) -> dict:
        """Zusammenfassung plus Einzelzustände für die Oberfläche."""
        with self._lock:
            konten = list(self.konten.values())
        einzeln = [{**k.status(), "username": k.username} for k in konten]
        gesamt = self.state
        # Das Konto, das gerade eine Eingabe braucht, nach vorn.
        wartend = next((e for e in einzeln if e["state"] == STATE_TWOFACTOR), None)
        fehler = next((e for e in einzeln if e.get("error_key")), None)
        vorne = wartend or fehler or (einzeln[0] if einzeln else {})
        return {
            "state": gesamt,
            "username": vorne.get("username"),
            "error_key": vorne.get("error_key"),
            "trusted_session": vorne.get("trusted_session", False),
            "session_valid_until": None,
            "throttled": any(e.get("throttled") for e in einzeln),
            "has_stored_password": any(e.get("has_stored_password") for e in einzeln),
            "accounts": einzeln,
        }


account = AccountManager()
