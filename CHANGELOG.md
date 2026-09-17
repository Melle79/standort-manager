# Änderungsverlauf

## 0.6.0 / 0.6.1 – 17.09.2026

**Eine abgelaufene Apple-Sitzung repariert sich jetzt selbst.**

Am 16.09. meldete Apple vier Sekunden nach dem Start „Authentication required
for Account (409)". Das Add-on hielt das für einen gewöhnlichen Abfragefehler,
schrieb ihn ins Protokoll und machte weiter — jede folgende Abfrage scheiterte
genauso. Dreiundzwanzig Minuten lang wurde nichts geortet, bis jemand von Hand
eingriff.

* Ein 409 oder 401 gilt jetzt als das, was er ist: abgelaufene Sitzung. Das
  Add-on meldet sich mit dem gespeicherten Passwort selbst neu an — bei einer
  vertrauenswürdigen Sitzung ohne Bestätigungscode.
* Höchstens alle fünf Minuten ein Versuch, damit bei einem echten
  Passwortproblem nicht gegen Apple gehämmert wird.
* Verlangt Apple doch einen Code, legt das Add-on eine Benachrichtigung in
  Home Assistant an. Bisher fiel der Stillstand erst auf, wenn jemand
  nachsah. Nach erfolgreicher Anmeldung verschwindet sie von selbst.
* Das gilt auch beim Start: Verlangt Apple schon beim Wiederaufnehmen der
  Sitzung einen Code, wird gemeldet. Bei einer Anmeldung über die Oberfläche
  nicht — da sitzt jemand davor und sieht die Abfrage ohnehin. Der Hinweis
  kommt einmal je Stillstand, nicht bei jedem Abfragezyklus.

**Weniger Rauschen im Protokoll**

* Der Wechsel zwischen iCloud und Companion-App wurde als Ereignis
  aufgezeichnet — an einem Tag 122-mal, allein 94-mal für ein Gerät. Im
  Ringpuffer verdrängte das genau die Meldungen, die zählen. Welche Quelle
  gerade gilt, steht weiterhin im Gerätezustand und in den MQTT-Attributen.
* „Gerät nicht erreichbar" wird nur noch für verfolgte Geräte gemeldet. Ein
  beobachtetes iPad im Schrank ist kein Ereignis.

## 0.5.0 – 14.09.2026

* **Die Zonen-Ansicht kann jetzt, was sie versprach.** Dort stand „Hier legst
  du fest, zu welchen die Entfernung berechnet wird" — festlegen ließ sich
  aber nichts. Jetzt lässt sich jede Zone einzeln ein- und ausschalten; eine
  abgeschaltete zählt nicht mehr als Aufenthaltsort. Die Heimzone bleibt fest.
  Die Liste zeigt außerdem, wer gerade in welcher Zone ist.
* **Das Protokoll nannte Geräte bei ihrer Apple-ID.** „Einstellung
  AWmmXT9RPCh6V0j8hZd/… geändert" statt des Gerätenamens.
* **Fehlermeldungen von Apple kamen als rohes HTML ins Protokoll** und
  machten es unlesbar. Sie werden jetzt auf ihre Aussage eingedampft: aus
  einer halben Webseite wird „Authentication required for Account (409)".

## 0.4.0 – 14.09.2026

* **Geräteliste zusammenklappbar.** Bei zwei Dutzend Geräten im Konto war die
  aufgeklappte Liste unbenutzbar lang. Ein Punkt vor dem Namen zeigt die
  Betriebsart, ohne dass man aufklappen muss: grün für Verfolgen, gelb für
  Nur beobachten, ein leerer Ring für Inaktiv.
* **Visueller Editor für die Dashboard-Karte.** Überschrift, Spaltenzahl,
  Adressanzeige und Geräteauswahl lassen sich im Dashboard einstellen, ohne
  YAML zu schreiben. Die Karte verwies schon auf einen Editor, den es nicht
  gab — „Karte bearbeiten" endete deshalb mit einem Fehler.
* **Karte und Editor liegen jetzt im Shadow DOM.** Ihre Klassennamen waren
  global und konnten andere Karten desselben Dashboards stören — im eigenen
  Editor tat eine fremde `.zeile`-Regel das prompt.

## 0.3.1 – 14.09.2026

* Der Hinweis auf Waze ist aus den Einstellungen verschwunden. Dass ein
  abgeschalteter Fremddienst dort erklärt wurde, half beim Bedienen nicht.
* Handbuch und README vollständig überarbeitet, mit Bildern.
* Testdaten enthalten keine wirklichen Orte und Namen mehr.

## 0.3.0 – 14.09.2026

Der Rest der Lücken gegenüber iCloud3 – und eine Bedienung, die sich selbst
erklärt.

* **Statusleiste statt Schalter in der Kopfzeile.** Der Schalter „Verfolgung"
  stand unbeschriftet neben der Sprachwahl und war doch der Hauptschalter der
  Ortung. Jetzt steht dort eine Zeile, die sagt, was läuft: „Verfolgung läuft ·
  5 Geräte · letzte Abfrage vor 2 Minuten", mit einer Schaltfläche daneben.
* **Dashboard-Karte.** `custom:standort-manager-card` zeigt die verfolgten
  Geräte. Das Add-on kopiert sie beim Start nach `/config/www` und meldet sie
  selbst als Lovelace-Ressource an – kein HACS, kein Eintrag von Hand. Die
  Karte liest nur Entitäten und folgt der Sprache von Home Assistant.
* **Mehrere Apple-Konten.** Für Familien, die auf mehr als eine Apple-ID
  verteilt sind. Jedes Konto hat eine eigene Sitzung; Befehle finden über die
  Geräte-ID ihr Konto. Bestehende Anmeldungen samt Sitzung ziehen automatisch
  mit um.
* **Gelernte Fahrzeiten statt Waze.** Waze' Routing-Schnittstelle ist
  abgeschaltet (410 Gone) – auch iCloud3 bekommt von dort nichts mehr. Statt
  einen Ersatzdienst mit Schlüsselzwang zu verlangen, misst die App selbst:
  geschätzte gegen tatsächliche Fahrtdauer, je Gerät, Ziel und Tageszeit, als
  Median über die letzten zwanzig Fahrten. Nach ein paar Fahrten sind die
  Schätzungen besser, als ein verkehrsfreier Dienst sie liefern kann.
* **Zeitversatz je Gerät** für Geräte in anderen Zeitzonen, mit eigenem
  Sensor für die Ortszeit.

## 0.2.1 – 14.09.2026

* Akkuwarnungen kamen für Geräte, die gar nicht verfolgt werden, und für
  AirPods und Uhren, die im Etui schlicht 0 statt gar nichts melden. Die
  Prüfung steht jetzt hinter der Betriebsart und ignoriert den Wert 0.

## 0.2.0 – 14.09.2026

Die Lücken gegenüber iCloud3 geschlossen, die im Alltag zählen.

* **Befehle ans iPhone:** Ton abspielen, Nachricht auf dem Sperrbildschirm,
  Verloren-Modus. Die ersten beiden auch als Schaltflächen in Home Assistant,
  der Verloren-Modus bewusst nur in der Oberfläche mit Rückfrage – er sperrt
  das Gerät.
* **Steuerung aus Automationen:** `switch.standort_tracking` hält die
  Verfolgung an, `button.standort_locate_all` ortet alle sofort, je Gerät gibt
  es `_locate`, `_sound` und `_message_send` samt Textfeld. Ein Add-on kann
  keine Dienste registrieren; Schaltflächen leisten in Automationen dasselbe.
* **Fahrtrichtung relativ zum Ziel** (`_direction_home`): nähert sich oder
  entfernt sich, statt nur einer Himmelsrichtung. Sprachneutral als
  `heading_key` im Tracker-Attribut.
* **Zweite Bezugszone je Gerät:** Entfernung, Fahrtdauer und Ankunft zu einem
  frei gewählten Ort neben dem Zuhause – etwa die Schule des Kindes.
* Vier weitere Sensoren: zurückgelegte Strecke, Entfernung zur nächstgelegenen
  Zone, Zeitpunkt der nächsten Abfrage, Akkuzustand.
* Geräte-, Zonen- und Einstellungsansicht zeichnen sich nicht mehr im
  15-Sekunden-Takt neu. Ein halb eingetippter Name blieb dabei nicht stehen.

## 0.1.5 – 14.09.2026

* Die Stationärzonen, die iCloud3 selbst anlegt (`zone.ic3_stationary_*`),
  werden übersprungen. Sie stehen dort, wo zufällig ein Gerät länger stand,
  wandern mit und verschwinden wieder – als Zone gewertet hätten sie
  Ankunfts- und Abfahrtsmeldungen an bedeutungslosen Orten erzeugt.

## 0.1.4 – 14.09.2026

Vier von fünf Geräten trugen weiter unlesbare Entitäts-IDs.

* Ursache war das Mitlesen der Companion-App: Es legt das Gerät an, noch bevor
  iCloud je geantwortet hat, und in diesem Moment steht in `name` noch die
  Apple-Geräte-ID. Daraus wurde eine Kurzform gebildet – und festgeschrieben,
  weil Kurzformen absichtlich nie wieder wechseln. Betroffen war genau, wer
  einen Companion-Tracker zugeordnet hatte.
* Das Mitlesen wartet jetzt, bis iCloud den Namen geliefert hat, und eine
  Notform ohne echten Namen wird nicht mehr gespeichert.

## 0.1.3 – 14.09.2026

Die Entitäten entstanden doch – nur unter anderen Namen, als sie sollten.

* **`object_id` wird von neueren Home-Assistant-Versionen ignoriert.** Die
  Entitäts-ID wurde stattdessen aus Geräte- und Entitätsname gebaut, also
  `device_tracker.iphone_von_finn_standort` statt
  `device_tracker.standort_iphone_von_finn`. Die Discovery setzt jetzt
  zusätzlich `default_entity_id`, das Feld, das tatsächlich greift.
* Die Entitäten trugen ein `_2` am Ende, weil die Karteileichen des ersten
  Versuchs den Namen noch belegten. Alle alten Discovery-Topics wurden
  entfernt, damit die Namen wieder frei sind.

## 0.1.2 – 14.09.2026

Es entstanden keine Entitäten in Home Assistant. Zwei Ursachen.

* **`via_device` zeigte ins Leere.** Der Discovery-Block verwies auf ein
  Brücken-Gerät, das nie angemeldet wurde. Home Assistant legt ein Gerät mit
  unbekanntem `via_device` stillschweigend nicht an – die Nachrichten lagen
  vollzählig auf dem Broker, im Kernprotokoll stand nicht einmal eine
  Warnung. Der Verweis ist entfernt.
* **Entitäts-IDs kamen aus Apples Geräte-ID.** Das ergab Namen wie
  `device_tracker.standort_aepuztvyafb6hykyy_irubg6y797hogwjury_cfz` – in
  einer Automation unbrauchbar. Die Kurzform kommt jetzt aus dem Gerätenamen
  (`standort_sven_s_iphone`), wird beim ersten Mal festgeschrieben und ändert
  sich beim Umbenennen nicht mehr. Gleichnamige Geräte werden durchnummeriert.
* `tests/test_mqtt.py` prüft beides mit.

## 0.1.1 – 14.09.2026

Zwei Fehler aus dem ersten Betrieb.

* **Anmeldung ging beim Neustart verloren.** Die Zugangsdaten wurden nur
  gespeichert, wenn Apple keinen Bestätigungscode verlangte – also praktisch
  nie. Nach einem Neustart des Add-ons war niemand mehr angemeldet. Jetzt
  wird gespeichert, sobald der Code angenommen ist.
* **Geräte zeigten das Modell statt ihres Namens.** Apple liefert beides
  getrennt: `name` ist der vergebene Gerätename, `deviceDisplayName` die
  Modellbezeichnung. Die Reihenfolge war vertauscht, wodurch zwei iPhones
  desselben Modells ununterscheidbar waren. Das Modell steht jetzt daneben,
  und zwar lesbar („iPhone 16 Pro Max" statt „iPhone16-1-1-0").
* Geräteliste sortiert: Verfolgtes zuerst, dann iPhone, iPad, Watch, AirPods,
  Mac. Bei zwei Dutzend Geräten im Konto sonst unbrauchbar.
* Tests unter `tests/` für beide Fehlerwege.

## 0.1.0 – 14.09.2026

Erste Fassung.

* Anmeldung am Apple-Konto über die Oberfläche, mit Bestätigungscode und
  dauerhafter Sitzung; Zugangsdaten verschlüsselt unter `/data`
* Standort, Zone, Entfernung, Akku, Genauigkeit, Tempo und Richtung je Gerät
* Adressauflösung über Nominatim, Fahrtdauer über OSRM
* Zustandsabhängiger Abfragetakt; Durchfahrtsprüfung für schnelle Quellen
* Companion-App der Home-Assistant-App als zweite, schnellere Standortquelle
* Veröffentlichung per MQTT-Discovery als `device_tracker` und elf Sensoren
  je Gerät
* Oberfläche mit Übersicht, Geräteverwaltung, Zonen, Protokoll, Einstellungen
* Deutsch und Englisch vollständig; Sprachwechsel zur Laufzeit, Protokoll
  wechselt rückwirkend mit
* `tools/check_translations.py` prüft Kataloge gegen den Code
