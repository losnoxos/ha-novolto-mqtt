# Novolto MQTT

*[English version](README.md)*

Home-Assistant-Integration (per HACS) für den Novolto Heizstab
(P2300/P3000) via MQTT. Spricht direkt mit demselben lokalen Broker, an dem
auch der Novolto selbst publiziert.

## Funktionsumfang

- Einrichtung per Config Flow (UI) – kein YAML nötig
- Mehrere Novolto-Geräte unterstützt, jedes als eigener Config-Entry
- `water_heater`-Entity: Ist- und Solltemperatur des Wassers
- `number`-Entities: Sollleistung, Hysterese
- `binary_sensor`: Heizt gerade (abgeleitet aus der Ist-Leistung, nicht aus
  dem im Test unzuverlässigen `rod_st`-Feld), dazu `rod_st` selbst als
  eigener diagnostischer Binary-Sensor
- `sensor`-Entities: Leistung, Spannung, Strom, Frequenz, Wassertemperatur,
  dekodierte Status-/Warnmeldungen, ein persistenter Energiezähler – dazu ein
  optionaler Board-Temperatursensor und diagnostische Sensoren (WLAN-Signal,
  Messintervall, Rohwerte für Status/Rod-Status/Triacon/Heizstufen – letztere
  vier nur zur Parität mit der Hersteller-App/Diagnose, nicht dokumentiert
  und nicht für Automatisierungen gedacht)
- Deutsche und englische UI-Übersetzung

## Voraussetzungen

- Home Assistant mit bereits eingerichteter Core-Integration `mqtt`,
  verbunden mit demselben Broker, an den der Novolto publiziert
- Das MQTT-Base-Topic des Novolto (seine Seriennummer, z.B.
  `AAA.BBB.123456` – sichtbar im MQTT Explorer)

## Installation per HACS

**Schnellweg:** Button unten klicken, in HACS bestätigen, danach weiter
bei Schritt 3.

[![Öffnet eure Home-Assistant-Instanz und ein Repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=losnoxos&repository=ha-novolto-mqtt&category=integration)

**Manueller Weg:**

1. HACS → ⋮-Menü → **Custom repositories** (Benutzerdefinierte Repositories)
2. `https://github.com/losnoxos/ha-novolto-mqtt` hinzufügen, Kategorie
   **Integration**
3. "Novolto MQTT" installieren, danach Home Assistant neu starten
4. Einstellungen → Geräte & Dienste → **Integration hinzufügen** → nach
   "Novolto MQTT" suchen
5. Base Topic (Serial) und optional einen Namen eingeben

## Optionen

Nach der Einrichtung über den **Konfigurieren**-Button der Integration
verfügbar: Topic-Suffixe, maximale Leistung/Schrittweite, Schwellwert
für "Heizt gerade", Grenzen für Solltemperatur und Hysterese,
Verfügbarkeits-Timeout sowie der optionale Board-Temperatursensor.

## Energiezähler

Der `Energie`-Sensor wird aus der Ist-Leistung (`avp`) integriert – derselbe
Ansatz wie bei dbus-novolto (`integrate` energy_source) – und persistent auf
Platte gespeichert, übersteht also HA-Neustarts. Bewusst **nicht** das
geräteeigene Feld `wel`, das bei jedem Novolto-Neustart auf 0 zurückspringt.
Der Sensor nutzt `state_class: total_increasing` und lässt sich direkt ins
Energie-Dashboard einbinden – kein zusätzlicher Riemann-Summe-Helfer nötig.

## Bekannte Einschränkungen

- Quittungen (`ret`/`s_err`) auf Einstellungsänderungen werden bisher nur
  als Warnung geloggt, noch nicht als Repair/Benachrichtigung in der UI
  angezeigt
- Keine MQTT-Auto-Discovery beim Einrichten – das Base Topic muss manuell
  eingegeben werden
- `rod_st`, `triacon`, `r1on`, `r2on` werden nur als rohe Diagnose-Sensoren
  abgebildet (für Parität/Fehlersuche) – vom Hersteller nicht dokumentiert,
  `rod_st` insbesondere fließt in keine Ein/Aus-Entscheidung ein (siehe
  `Heizt`-Binary-Sensor)

## Protokoll-Referenz

Siehe
[NOVOLTO-MQTT.md](https://github.com/losnoxos/dbus-novolto/blob/main/NOVOLTO-MQTT.md)
in dbus-novolto für die vollständige Feld- und Protokoll-Referenz (diese
Integration folgt demselben Protokoll).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
