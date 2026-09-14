"""Gelernte Fahrzeiten.

Warum überhaupt: Routendienste rechnen mit freier Straße. Die tatsächliche
Fahrt dauert je nach Uhrzeit deutlich länger. Waze konnte das früher
beantworten, seine Routing-Schnittstelle ist aber abgeschaltet (410 Gone),
und jeder Dienst mit Echtzeitverkehr verlangt einen eigenen Schlüssel.

Also misst die App selbst: Bei jeder Fahrt wird die geschätzte Dauer
festgehalten und bei der Ankunft mit der tatsächlichen verglichen. Aus dem
Verhältnis entsteht ein Korrekturfaktor je Gerät, Ziel und Tageszeit. Nach
ein paar Fahrten sind die Schätzungen damit besser, als ein verkehrsfreier
Dienst sie je liefern könnte - und es kostet keine einzige Anfrage.

Der Median statt des Mittelwerts, weil eine einzige Vollsperrung sonst
wochenlang nachwirkt.
"""

import logging
import os
import sqlite3
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("standort.fahrten")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_DATEI = DATA_DIR / "fahrten.db"

# Eine Fahrt, die länger als das dauert, war keine Fahrt mehr, sondern ein
# Gerät, das unterwegs stehen blieb.
MAX_FAHRTDAUER = 4 * 3600
# Unter dieser Dauer ist das Verhältnis zu ungenau, um daraus zu lernen.
MIN_FAHRTDAUER = 180
# So viele Fahrten braucht es, bevor ein Faktor angewendet wird.
MIN_FAHRTEN = 3
# Ausreißer: alles jenseits dieser Spanne war kein normaler Verkehr.
FAKTOR_GRENZEN = (0.5, 4.0)


def _tagesfenster(zeitpunkt: float) -> str:
    """Grobe Tageszeit.

    Vier Fenster statt 24 Stunden: feiner aufgelöst dauerte es Monate, bis
    genug Fahrten je Fenster zusammenkommen.
    """
    stunde = datetime.fromtimestamp(zeitpunkt).hour
    werktag = datetime.fromtimestamp(zeitpunkt).weekday() < 5
    if not werktag:
        return "wochenende"
    if 6 <= stunde < 10:
        return "morgens"
    if 15 <= stunde < 19:
        return "abends"
    return "sonst"


class Fahrtenbuch:
    def __init__(self):
        self._lock = threading.RLock()
        self._offen: dict[tuple, dict] = {}
        self._db = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(DB_DATEI, check_same_thread=False)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS fahrten (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    geraet TEXT NOT NULL,
                    ziel TEXT NOT NULL,
                    fenster TEXT NOT NULL,
                    geschaetzt REAL NOT NULL,
                    tatsaechlich REAL NOT NULL,
                    faktor REAL NOT NULL,
                    ende REAL NOT NULL
                )
            """)
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_suche "
                "ON fahrten (geraet, ziel, fenster, ende)")
            self._db.commit()
        except sqlite3.Error as err:
            log.warning("Fahrtenbuch nicht nutzbar: %s", err)
            self._db = None

    # ------------------------------------------------------------ Aufzeichnen

    def fahrt_beginnt(self, geraet: str, ziel: str, geschaetzt_s: float) -> None:
        """Eine Fahrt eröffnen, sobald zum ersten Mal eine Dauer geschätzt wird.

        Mehrfaches Aufrufen während derselben Fahrt ändert nichts - die
        erste Schätzung zählt, denn nur sie stand vor der Fahrt zur
        Verfügung.
        """
        if not ziel or geschaetzt_s <= 0:
            return
        schluessel = (geraet, ziel)
        with self._lock:
            if schluessel in self._offen:
                return
            self._offen[schluessel] = {
                "start": time.time(),
                "geschaetzt": geschaetzt_s,
            }

    def fahrt_endet(self, geraet: str, ziel: str) -> dict | None:
        """Eine Fahrt abschließen und daraus lernen."""
        schluessel = (geraet, ziel)
        with self._lock:
            eintrag = self._offen.pop(schluessel, None)
        if not eintrag:
            return None

        dauer = time.time() - eintrag["start"]
        if not (MIN_FAHRTDAUER <= dauer <= MAX_FAHRTDAUER):
            return None

        faktor = dauer / eintrag["geschaetzt"]
        if not (FAKTOR_GRENZEN[0] <= faktor <= FAKTOR_GRENZEN[1]):
            return None

        if self._db:
            try:
                with self._lock:
                    self._db.execute(
                        "INSERT INTO fahrten (geraet, ziel, fenster, geschaetzt, "
                        "tatsaechlich, faktor, ende) VALUES (?,?,?,?,?,?,?)",
                        (geraet, ziel, _tagesfenster(eintrag["start"]),
                         eintrag["geschaetzt"], dauer, faktor, time.time()))
                    self._db.commit()
            except sqlite3.Error as err:
                log.warning("Fahrt nicht speicherbar: %s", err)

        return {"geschaetzt": round(eintrag["geschaetzt"]),
                "tatsaechlich": round(dauer), "faktor": round(faktor, 2)}

    def fahrt_abbrechen(self, geraet: str, ziel: str | None = None) -> None:
        """Offene Fahrten verwerfen, etwa wenn das Gerät umkehrt."""
        with self._lock:
            for schluessel in list(self._offen):
                if schluessel[0] == geraet and (ziel is None or schluessel[1] == ziel):
                    del self._offen[schluessel]

    # ----------------------------------------------------------- Anwenden

    def faktor(self, geraet: str, ziel: str, zeitpunkt: float | None = None) -> float:
        """Korrekturfaktor für eine Schätzung.

        Reihenfolge: dieses Gerät zu diesem Ziel in diesem Tagesfenster,
        sonst dasselbe ohne Fenster, sonst 1.0. Lieber gar nicht korrigieren
        als mit fremden Daten.
        """
        if not self._db or not ziel:
            return 1.0
        fenster = _tagesfenster(zeitpunkt or time.time())
        for bedingung, werte in (
            ("geraet = ? AND ziel = ? AND fenster = ?", (geraet, ziel, fenster)),
            ("geraet = ? AND ziel = ?", (geraet, ziel)),
        ):
            werte_liste = self._faktoren(bedingung, werte)
            if len(werte_liste) >= MIN_FAHRTEN:
                return round(statistics.median(werte_liste), 3)
        return 1.0

    def _faktoren(self, bedingung: str, werte: tuple) -> list:
        try:
            with self._lock:
                # Nur die jüngsten Fahrten: Baustellen und Umzüge sollen
                # nicht ewig nachwirken.
                zeilen = self._db.execute(
                    f"SELECT faktor FROM fahrten WHERE {bedingung} "
                    "ORDER BY ende DESC LIMIT 20", werte).fetchall()
            return [z[0] for z in zeilen]
        except sqlite3.Error:
            return []

    def statistik(self, geraet: str | None = None) -> dict:
        """Was das Fahrtenbuch bisher weiß - für die Oberfläche."""
        if not self._db:
            return {"fahrten": 0, "ziele": []}
        try:
            with self._lock:
                if geraet:
                    gesamt = self._db.execute(
                        "SELECT COUNT(*) FROM fahrten WHERE geraet = ?",
                        (geraet,)).fetchone()[0]
                    zeilen = self._db.execute(
                        "SELECT ziel, fenster, COUNT(*), AVG(faktor) FROM fahrten "
                        "WHERE geraet = ? GROUP BY ziel, fenster", (geraet,)).fetchall()
                else:
                    gesamt = self._db.execute(
                        "SELECT COUNT(*) FROM fahrten").fetchone()[0]
                    zeilen = self._db.execute(
                        "SELECT ziel, fenster, COUNT(*), AVG(faktor) FROM fahrten "
                        "GROUP BY ziel, fenster").fetchall()
            return {
                "fahrten": gesamt,
                "ziele": [{"ziel": z[0], "fenster": z[1], "anzahl": z[2],
                           "faktor": round(z[3], 2)} for z in zeilen],
                "offen": len(self._offen),
            }
        except sqlite3.Error:
            return {"fahrten": 0, "ziele": []}

    def aufraeumen(self, tage: int = 180) -> int:
        """Alte Fahrten entfernen."""
        if not self._db:
            return 0
        grenze = time.time() - tage * 86400
        try:
            with self._lock:
                cur = self._db.execute("DELETE FROM fahrten WHERE ende < ?", (grenze,))
                self._db.commit()
                return cur.rowcount
        except sqlite3.Error:
            return 0


fahrtenbuch = Fahrtenbuch()
