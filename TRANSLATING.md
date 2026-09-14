# Eine Sprache hinzufügen

Der Standort-Manager braucht für eine neue Sprache genau eine Datei. Kein
Eingriff in den Code, kein Neubau des Images.

## In drei Schritten

1. **Vorlage kopieren.** `standort_manager/translations/en.json` ist die
   Referenz – sie enthält immer alle Schlüssel.

   ```bash
   cp standort_manager/translations/en.json standort_manager/translations/fr.json
   ```

   Der Dateiname ist der Sprachcode (`fr`, `nl`, `it`, …).

2. **`meta` anpassen und übersetzen.**

   ```json
   {
     "meta": {
       "name": "Français",     // wie die Sprache in ihrer eigenen Sprache heißt
       "code": "fr",
       "translator": "Dein Name",
       "direction": "ltr"
     },
     "strings": { ... }
   }
   ```

   `name` ist das, was in der Sprachauswahl steht – also die Sprache in sich
   selbst benannt, nicht auf Deutsch.

3. **Prüfen.**

   ```bash
   python3 tools/check_translations.py
   ```

Nach einem Neustart des Add-ons steht die Sprache in der Auswahl. Unvollständige
Sprachen erscheinen dort mit ihrem Deckungsgrad, etwa „Français (78 %)".

## Regeln

**Platzhalter bleiben, wie sie sind.** `{device}`, `{zone}`, `{count}` werden
zur Laufzeit ersetzt. Ihre Reihenfolge im Satz darf sich ändern, ihre Schreibung
nicht:

```json
"device_arrived": "{device} hat {zone} erreicht"
"device_arrived": "{device} reached {zone}"
```

Das Prüfwerkzeug meldet es, wenn eine Sprache einen Platzhalter verliert oder
erfindet.

**Zonennamen werden nicht übersetzt.** Sie kommen als Eigennamen aus Home
Assistant. Steht dort „Zuhause", heißt es auch im englischen Protokoll
„reached Zuhause". Das ist Absicht – der Benutzer erkennt seine eigene Zone
wieder.

**Kurz halten, wo es eng ist.** Die Schlüssel unter `sensor.` werden zu
Entitätsnamen in Home Assistant, `unit.` steht in Tabellenzellen. Ein langer
Satz bricht dort die Anzeige.

**Fehlende Schlüssel sind erlaubt.** Was fehlt, kommt automatisch auf Englisch.
Eine halbfertige Übersetzung ist also brauchbar, nicht kaputt.

## Was passiert beim Umschalten

Die Oberfläche lädt den Katalog neu und zeichnet sich neu – ohne Seitenneuladen.
Das Ereignisprotokoll wechselt **rückwirkend** mit, weil es Schlüssel speichert
statt Sätze. Die Entitäten in Home Assistant bekommen neue Anzeigenamen, behalten
aber ihre IDs, damit Automationen weiterlaufen.
