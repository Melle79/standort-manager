"""Prüft die Umstellung von einem auf mehrere Apple-Konten.

Der heikle Teil ist die Migration: Wer vorher angemeldet war, muss es nach
dem Update bleiben. Sonst steht der Benutzer ohne Vorwarnung wieder vor der
Code-Abfrage und weiß nicht, warum.
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile

BASE = str(pathlib.Path(__file__).resolve().parent.parent / "standort_manager")
DATA = tempfile.mkdtemp(prefix="stm-konten-")
os.environ.update(DATA_DIR=DATA, TRANSLATIONS_DIR=f"{BASE}/translations")
sys.path.insert(0, f"{BASE}/app")

from cryptography.fernet import Fernet

# --- Eine alte Konfiguration nachstellen, wie sie vor dem Update aussah ---
schluessel = Fernet.generate_key()
pathlib.Path(DATA, "secret.key").write_bytes(schluessel)
verschluesselt = Fernet(schluessel).encrypt(b"geheim").decode()
pathlib.Path(DATA, "config.json").write_text(json.dumps({
    "sprache": "de",
    "apple": {"username": "alt@example.com", "password": verschluesselt},
    "geraete": {"ABC123": {"modus": "track", "name": "Handy A"}},
}), encoding="utf-8")

import store as store_mod  # noqa: E402 - erst nach dem Anlegen importieren

store = store_mod.store

print("1. Alte Einzelkonto-Konfiguration wird übernommen")
konten = store.accounts()
print("   Konten nach dem Laden:", konten)
assert konten == ["alt@example.com"], konten
benutzer, passwort = store.credentials()
assert (benutzer, passwort) == ("alt@example.com", "geheim"), (benutzer, passwort)
print(f"   Zugangsdaten erhalten: {benutzer}, Passwort {'ja' if passwort else 'nein'}")

print("2. Der alte Schlüssel ist weg, die Geräte sind geblieben")
roh = json.loads(pathlib.Path(DATA, "config.json").read_text(encoding="utf-8"))
assert "apple" not in roh, "Der alte Einzelkonto-Eintrag steht noch da"
assert roh["geraete"]["ABC123"]["name"] == "Handy A"
print("   'apple' entfernt, Geräteeinstellungen unberührt")

print("3. Zweites Konto dazu")
store.set_credentials("zweit@example.com", "anderes")
assert store.accounts() == ["alt@example.com", "zweit@example.com"]
assert store.credentials("zweit@example.com")[1] == "anderes"
assert store.credentials("alt@example.com")[1] == "geheim"
print("   beide Konten getrennt abrufbar")

print("4. Kein Passwort steht im Klartext auf der Platte")
roh_text = pathlib.Path(DATA, "config.json").read_text(encoding="utf-8")
assert "geheim" not in roh_text and "anderes" not in roh_text
print("   config.json enthält keines der Passwörter")

print("5. Getrennte Sitzungsverzeichnisse je Konto")
from apple import AppleAccount  # noqa: E402

a = AppleAccount._session_dir("alt@example.com")
b = AppleAccount._session_dir("zweit@example.com")
assert a != b, "Beide Konten teilen sich ein Cookie-Verzeichnis"
print(f"   {a.name} != {b.name}")

print("5b. Die Sitzung des Einzelkontos zieht mit um")
from apple import _sitzung_migrieren, SESSION_DIR  # noqa: E402

SESSION_DIR.mkdir(parents=True, exist_ok=True)
(SESSION_DIR / "altekonto.cookiejar").write_text("cookies", encoding="utf-8")
(SESSION_DIR / "altekonto.session").write_text("{}", encoding="utf-8")
_sitzung_migrieren(["alt@example.com"])
umgezogen = AppleAccount._session_dir("alt@example.com")
assert (umgezogen / "altekonto.cookiejar").is_file(), "Cookies nicht umgezogen"
assert not (SESSION_DIR / "altekonto.cookiejar").exists(), "Alte Datei blieb liegen"
print(f"   2 Dateien nach {umgezogen.name} verschoben")

print("6. Ein Konto entfernen lässt das andere stehen")
store.remove_account("alt@example.com")
assert store.accounts() == ["zweit@example.com"]
assert store.credentials("zweit@example.com")[1] == "anderes"
print("   verbleibendes Konto unversehrt")

print("7. Der Manager meldet sich wie ein Einzelkonto, solange es eines ist")
from apple import AccountManager, STATE_LOGGED_OUT  # noqa: E402

manager = AccountManager()
assert manager.state == STATE_LOGGED_OUT
zustand = manager.status()
assert "accounts" in zustand and zustand["accounts"] == []
print("   Status ohne Konten:", zustand["state"])

shutil.rmtree(DATA, ignore_errors=True)
print("\nAlle Prüfungen bestanden.")
