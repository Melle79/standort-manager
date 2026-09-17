"""Prüft, ob die App eine abgelaufene Apple-Sitzung selbst erneuert.

Am 16.09.2026 meldete Apple vier Sekunden nach dem Start
„Authentication required for Account (409)". Die App hielt das für einen
gewöhnlichen Abfragefehler, protokollierte ihn und machte weiter — jede
folgende Abfrage scheiterte genauso. Dreiundzwanzig Minuten lang wurde
nichts geortet, bis jemand von Hand eingriff.
"""
import os
import pathlib
import shutil
import sys
import tempfile

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
DATA = tempfile.mkdtemp(prefix="stm-sitzung-")
os.environ.update(DATA_DIR=DATA, TRANSLATIONS_DIR=f"{BASE}/translations")
sys.path.insert(0, f"{BASE}/app")

import apple  # noqa: E402
import ha  # noqa: E402
from pyicloud.exceptions import PyiCloudAPIResponseException  # noqa: E402
from store import store  # noqa: E402

# Benachrichtigungen nur mitschreiben, nicht verschicken.
gemeldet = []
ha.melde = lambda titel, text, kennung="standort_manager": gemeldet.append(text)
ha.meldung_zuruecknehmen = lambda kennung="standort_manager": gemeldet.clear()

store.set_credentials("test@example.com", "geheim")


class FakeDevices:
    """Tut so, als sei die Sitzung abgelaufen - bis sie erneuert wird."""

    def __init__(self, api):
        self._api = api

    def refresh(self, locate=True):
        if self._api.sitzung_tot:
            raise PyiCloudAPIResponseException(
                "Authentication required for Account. (409)")

    def __iter__(self):
        return iter([])


class FakeApi:
    verlangt_code = False

    def __init__(self, *a, **kw):
        # Eine neue Anmeldung bringt eine frische Sitzung mit.
        self.sitzung_tot = False
        self.requires_2fa = FakeApi.verlangt_code
        self.requires_2sa = False
        self.is_trusted_session = True
        self.devices = FakeDevices(self)


apple.PyiCloudService = FakeApi

konto = apple.AppleAccount("test@example.com")
konto.login("test@example.com", "geheim")
assert konto.state == apple.STATE_READY, konto.state
print("1. Angemeldet:", konto.state)

print("2. Sitzung stirbt mitten im Betrieb")
konto._api.sitzung_tot = True
konto._letzte_wiederanmeldung = 0          # Sperre für den Test aufheben
ergebnis = konto.fetch()
assert ergebnis == [], ergebnis
print("   Zustand nach der Abfrage:", konto.state)
assert konto.state == apple.STATE_READY, (
    "Die App hat sich nicht selbst neu angemeldet — genau der Fehler vom 16.09.")
print("   Sie hat sich selbst neu angemeldet.")

print("3. Zweiter Versuch innerhalb der Sperrzeit wird nicht wiederholt")
konto._api.sitzung_tot = True
vorher = konto._letzte_wiederanmeldung
konto.fetch()
assert konto._letzte_wiederanmeldung == vorher, (
    "Die Sperre greift nicht — die App würde gegen Apple hämmern")
print("   Sperre greift, kein Dauerversuch.")

print("4. Verlangt Apple einen Code, wird der Benutzer benachrichtigt")
gemeldet.clear()
FakeApi.verlangt_code = True
konto._letzte_wiederanmeldung = 0
konto._api.sitzung_tot = True
konto.fetch()
assert konto.state == apple.STATE_TWOFACTOR, konto.state
assert gemeldet, "Keine Benachrichtigung in Home Assistant angelegt"
print("   Zustand:", konto.state)
print("   Benachrichtigung:", gemeldet[0].splitlines()[0][:60] + " …")

print("4b. Der Hinweis kommt nur einmal, nicht bei jedem Zyklus")
gemeldet.clear()
konto._letzte_wiederanmeldung = 0
konto._api.sitzung_tot = True
konto.fetch()
assert not gemeldet, "Der Hinweis wurde erneut gemeldet"
print("   zweiter Zyklus meldet nichts mehr")

print("4c. Auch beim Start wird gemeldet, wenn Apple einen Code will")
gemeldet.clear()
neues = apple.AppleAccount("test@example.com")
neues._codebedarf_gemeldet = False
neues.login("test@example.com", "geheim", from_restore=True)
assert neues.state == apple.STATE_TWOFACTOR, neues.state
assert gemeldet, ("Beim Start ohne Benachrichtigung - genau die Luecke, die "
                  "am 17.09. auffiel")
print("   Benachrichtigung auch beim Start vorhanden")

print("4d. Bei einer Anmeldung ueber die Oberflaeche nicht melden")
gemeldet.clear()
handisch = apple.AppleAccount("test@example.com")
handisch.login("test@example.com", "geheim")     # from_restore=False
assert handisch.state == apple.STATE_TWOFACTOR
assert not gemeldet, "Wer selbst anmeldet, sieht die Code-Abfrage ohnehin"
print("   kein Hinweis - der Benutzer sitzt davor")

print("5. Ohne gespeichertes Passwort geht es nicht ohne den Benutzer")
store.set_credentials("test@example.com", None)
gemeldet.clear()
konto2 = apple.AppleAccount("test@example.com")
konto2.state = apple.STATE_READY
konto2._api = FakeApi()
konto2._api.sitzung_tot = True
konto2._letzte_wiederanmeldung = 0
konto2.fetch()
assert konto2.state == apple.STATE_TWOFACTOR, konto2.state
assert gemeldet, "Auch hier muss der Benutzer erfahren, dass nichts läuft"
print("   Zustand:", konto2.state, "— und der Benutzer wurde benachrichtigt")

shutil.rmtree(DATA, ignore_errors=True)
print("\nAlle Prüfungen bestanden.")
