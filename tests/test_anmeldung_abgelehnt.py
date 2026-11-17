"""Apple lehnt die Anmeldung ab - die App darf nicht still liegen bleiben.

Am 28.10.2026 meldete Apple bei jeder Abfrage „Invalid email/password
combination.“ Die App fragte zwei Stunden lang alle 15 Sekunden weiter,
über 400-mal. Nach einem Neustart scheiterte das Wiederanknüpfen mit
demselben Fehler - und weil das beim Neustart geschah, stand weder etwas im
Protokoll noch kam eine Meldung. Zwanzig Tage lang wurde nichts geortet.
"""
import os
import pathlib
import sys
import tempfile

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
DATA = tempfile.mkdtemp(prefix="stm-abgelehnt-")
os.environ.update(DATA_DIR=DATA, TRANSLATIONS_DIR=f"{BASE}/translations")
sys.path.insert(0, f"{BASE}/app")

import apple  # noqa: E402
import ha  # noqa: E402
from events import events  # noqa: E402
from pyicloud.exceptions import (  # noqa: E402
    PyiCloudAPIResponseException, PyiCloudFailedLoginException)
from store import store  # noqa: E402

gemeldet, handy = [], []
ha.melde = lambda titel, text, kennung="standort_manager": gemeldet.append(text)
ha.meldung_zuruecknehmen = lambda kennung="standort_manager": gemeldet.clear()

uhr = [1_000_000.0]
apple.time.time = lambda: uhr[0]

store.set_credentials("test@example.com", "geheim")


class FakeDevices:
    def __init__(self):
        self.fehler = None
        self.aufrufe = 0

    def refresh(self, locate=True):
        self.aufrufe += 1
        if self.fehler:
            raise self.fehler

    def __iter__(self):
        return iter([])


class FakeApi:
    lehnt_ab = True
    anmeldungen = 0

    def __init__(self, *a, **kw):
        FakeApi.anmeldungen += 1
        if FakeApi.lehnt_ab:
            raise PyiCloudFailedLoginException("Invalid email/password combination.")
        self.requires_2fa = self.requires_2sa = False
        self.is_trusted_session = True
        self.devices = FakeDevices()


apple.PyiCloudService = FakeApi
fehler = []


def schluessel():
    return [e["key"] for e in events.list("de")]


# 1. Neustart, Apple lehnt ab: Protokoll UND Meldung.
konto = apple.AppleAccount("test@example.com")
konto.restore()
if konto.state != apple.STATE_ERROR:
    fehler.append(f"Zustand nach Ablehnung: {konto.state}")
if "event.login_failed" not in schluessel():
    fehler.append("abgelehnte Anmeldung beim Neustart fehlt im Protokoll")
if len(gemeldet) != 1:
    fehler.append(f"erwartet eine Meldung in HA, waren {len(gemeldet)}")

# 2. Abfragen im Fehlerzustand: erst nach der Pause ein neuer Versuch,
#    und keine zweite Meldung.
vorher = FakeApi.anmeldungen
konto.fetch()
if FakeApi.anmeldungen != vorher + 1:
    fehler.append("erster Wiederversuch muss sofort kommen")
konto.fetch()
if FakeApi.anmeldungen != vorher + 1:
    fehler.append("vor Ablauf der Pause kein weiterer Versuch")
if len(gemeldet) != 1:
    fehler.append("Meldung darf nicht wiederholt werden")

# 3. Nach höchstens sechs Versuchen Schluss - sonst sperrt Apple das Konto.
for _ in range(50):
    uhr[0] += 3600
    konto.fetch()
versuche = FakeApi.anmeldungen - vorher
if versuche != apple.WIEDERANMELDUNG_MAX_VERSUCHE:
    fehler.append(f"{versuche} Wiederversuche statt "
                  f"{apple.WIEDERANMELDUNG_MAX_VERSUCHE}")

# 4. Neu angemeldet: Meldung verschwindet von selbst.
FakeApi.lehnt_ab = False
konto.login("test@example.com", "geheim")
if konto.state != apple.STATE_READY or gemeldet:
    fehler.append("nach Anmeldung muss die Meldung weg sein")

# 5. Derselbe Text mitten im Betrieb gilt als verworfene Sitzung.
konto._api.devices.fehler = PyiCloudAPIResponseException(
    "Invalid email/password combination.")
uhr[0] += 3600
vorher = FakeApi.anmeldungen
konto.fetch()
if FakeApi.anmeldungen != vorher + 1:
    fehler.append("'Invalid email/password' beim Abfragen muss neu anmelden")

# 6. Ein gewöhnlicher Dauerfehler: Pause statt 15-Sekunden-Takt.
konto._api.devices.fehler = PyiCloudAPIResponseException("Irgendwas (500)")
api = konto._api
for _ in range(20):          # 20 Zyklen à 15 s = 5 Minuten
    konto.fetch()
    uhr[0] += 15
if api.devices.aufrufe > 4:
    fehler.append(f"{api.devices.aufrufe} Abfragen in 5 Minuten trotz Fehler")

# 7. Handy-Meldung nur mit gewähltem Dienst.
import importlib  # noqa: E402
ha = importlib.reload(ha)
gesendet = []
ha.available = lambda: True
ha.requests.post = lambda url, **kw: gesendet.append(url) or type(
    "R", (), {"ok": True})()
ha.melde("T", "x")
if any("/notify/" in u for u in gesendet):
    fehler.append("ohne gewählten Dienst keine Handy-Meldung")
store.set("benachrichtigung", "notify.mobile_app_handy")
ha.melde("T", "x")
if not any(u.endswith("/notify/mobile_app_handy") for u in gesendet):
    fehler.append("Handy-Meldung an gewählten Dienst fehlt")

if fehler:
    print("FEHLER:"); [print("  -", f) for f in fehler]; sys.exit(1)
print("Abgelehnte Anmeldung: alle Erwartungen erfüllt.")
