# Novolto MQTT

*[English version](README.md)*

Home-Assistant-Integration (per HACS) für den Novolto Heizstab
(P2300/P3000) via MQTT. Spricht direkt mit demselben lokalen Broker, an dem
auch der Novolto selbst publiziert – **kein Venus OS/Victron-dbus nötig**.
Protokoll-kompatibles Schwesterprojekt zu
[dbus-novolto](https://github.com/losnoxos/dbus-novolto) (Venus-OS-Treiber)
und [heatpump-novolto](https://github.com/losnoxos/victronenergy.heatpump.novolto).

## Funktionsumfang

- Einrichtung per Config Flow (UI) – kein YAML nötig
- Mehrere Novolto-Geräte unterstützt, jedes als eigener Config-Entry
- `water_heater`-Entity: Ist- und Solltemperatur des Wassers
- `number`-Entities: Sollleistung, Hysterese
- `binary_sensor`: Heizt gerade (abgeleitet aus der Ist-Leistung, nicht aus
  dem im Test unzuverlässigen `rod_st`-Feld)
- `sensor`-Entities: Spannung, Strom, Frequenz, Wassertemperatur, dekodierte
  Status-/Warnmeldungen – dazu ein optionaler Board-Temperatursensor und
  diagnostische WLAN-/Messintervall-Sensoren (standardmäßig deaktiviert)
- Deutsche und englische UI-Übersetzung

## Voraussetzungen

- Home Assistant mit bereits eingerichteter Core-Integration `mqtt`,
  verbunden mit demselben Broker, an den der Novolto publiziert
- Das MQTT-Base-Topic des Novolto (seine Seriennummer, z.B.
  `AAA.BBB.123456` – sichtbar im MQTT Explorer)

## Installation per HACS

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

Diese Integration bringt bewusst **keinen eigenen Energiezähler** mit.
Stattdessen HAs eingebauten
[Riemann-Summe-Integral](https://www.home-assistant.io/integrations/integration/)-Helfer
auf den Leistungssensor legen – einfacher und robuster als eine eigene
Persistenz nachzubauen, und er lässt sich direkt ins Energie-Dashboard
einbinden. (Das geräteeigene Feld `wel` springt bei jedem Novolto-Neustart
auf 0 zurück, deshalb wird es hier nicht verwendet.)

## Bekannte Einschränkungen

- Quittungen (`ret`/`s_err`) auf Einstellungsänderungen werden bisher nur
  als Warnung geloggt, noch nicht als Repair/Benachrichtigung in der UI
  angezeigt
- Keine MQTT-Auto-Discovery beim Einrichten – das Base Topic muss manuell
  eingegeben werden
- `rod_st`, `triacon`, `r1on`, `r2on` werden bewusst nicht abgebildet –
  laut Hersteller unzuverlässig bzw. nicht dokumentiert

## Protokoll-Referenz

Siehe
[NOVOLTO-MQTT.md](https://github.com/losnoxos/dbus-novolto/blob/main/NOVOLTO-MQTT.md)
in dbus-novolto für die vollständige Feld- und Protokoll-Referenz (diese
Integration folgt demselben Protokoll).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
