"""Prüft: Überlebt eine Anmeldung MIT Bestätigungscode den Neustart?

Genau dieser Weg hat die Zugangsdaten verloren - die Anmeldung galt erst
nach dem Code als fertig, gespeichert wurde aber nur im Weg ohne Code.
"""
import os, pathlib, sys, tempfile, shutil

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
DATA = tempfile.mkdtemp(prefix="stm-2fa-")
os.environ.update(DATA_DIR=DATA, TRANSLATIONS_DIR=f"{BASE}/translations")
sys.path.insert(0, f"{BASE}/app")

import apple
from store import store


class FakeApi:
    """Verhält sich wie ein Apple-Konto mit Zwei-Faktor-Pflicht."""
    def __init__(self, *a, **kw):
        self.requires_2fa = True
        self.requires_2sa = False
        self.is_trusted_session = False
        self.devices = []

    def validate_2fa_code(self, code):
        if code == "123456":
            self.requires_2fa = False
            return True
        return False

    def trust_session(self):
        self.is_trusted_session = True
        return True


apple.PyiCloudService = FakeApi
konto = apple.AppleAccount()

print("1. Anmelden mit Apple-ID und Passwort")
zustand = konto.login("test@example.com", "geheim", store_password=True)
print("   Zustand:", zustand["state"])
assert zustand["state"] == "twofactor", zustand

benutzer, passwort = store.credentials()
print(f"   Gespeichert vor dem Code: Benutzer={benutzer!r} Passwort={'ja' if passwort else 'nein'}")
assert benutzer is None, "Vor dem Code darf noch nichts gespeichert sein"

print("2. Falschen Code einreichen")
zustand = konto.submit_2fa("000000")
print("   Zustand:", zustand["state"], "| Fehler:", zustand["error_key"])
assert zustand["error_key"] == "error.bad_code"
assert store.credentials()[0] is None, "Falscher Code darf nichts speichern"

print("3. Richtigen Code einreichen")
zustand = konto.submit_2fa("123456")
print("   Zustand:", zustand["state"])
assert zustand["state"] == "ready", zustand

benutzer, passwort = store.credentials()
print(f"   Gespeichert nach dem Code: Benutzer={benutzer!r} Passwort={'ja' if passwort else 'nein'}")
assert benutzer == "test@example.com", "Benutzer fehlt - Neustart würde abmelden"
assert passwort == "geheim", "Passwort fehlt - Wiederanmeldung unmöglich"

print("4. Passwort darf nicht im Klartext auf der Platte stehen")
roh = open(os.path.join(DATA, "config.json"), encoding="utf-8").read()
assert "geheim" not in roh, "Passwort steht im Klartext!"
print("   config.json enthält 'geheim' nicht im Klartext")

print("5. Neustart nachstellen: neuer Store, neues Konto")
store.load()
benutzer2, passwort2 = store.credentials()
print(f"   Nach Neustart: Benutzer={benutzer2!r} Passwort={'ja' if passwort2 else 'nein'}")
assert benutzer2 == "test@example.com" and passwort2 == "geheim"

print("6. Abmelden mit 'vergessen' räumt auf")
konto.logout(forget=True)
assert store.credentials() == (None, None)
print("   Zugangsdaten gelöscht")

shutil.rmtree(DATA, ignore_errors=True)
print("\nAlle Prüfungen bestanden.")
