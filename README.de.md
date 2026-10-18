# Novolto MQTT

*[English version](README.md)*

Home-Assistant-Integration für den Novolto-Heizstab (P2300/P3000). Der
Heizstab sendet seine Messwerte an einen MQTT-Broker in deinem Heimnetz,
Home Assistant liest sie dort mit und schickt Einstellungen zurück. Alles
bleibt lokal, ohne Cloud.

## Was du bekommst

**Damit steuerst du den Heizstab**

| Entität | Wozu |
|---|---|
| *Gerätename* (Warmwasserbereiter) | Ist- und Solltemperatur. Zustand: **Heizt**, **Bereit** oder **Aus (0 W)** |
| Leistungsstufe (Auswahl) | Feste Stufen per Klick, z. B. Aus / 1000 / 2000 / 3000 W |
| Sollleistung (Zahl) | Leistung frei einstellen, 0 W = aus |
| Schaltdifferenz (Hysterese) (Zahl) | Wie weit die Temperatur um den Sollwert schwanken darf |

**Nur Anzeige**

Heizt (an/aus), Leistung, Energie (für das Energie-Dashboard),
Wassertemperatur, Elektroniktemperatur, Spannung, Strom, Frequenz, Status.
Standardmäßig ausgeblendet und bei Bedarf einschaltbar: WLAN-Signal,
Messintervall und Rohwerte für die Fehlersuche.

## Was du brauchst

- Home Assistant ab Version 2024.12
- [HACS](https://hacs.xyz)
- Einen Novolto-Heizstab P2300 oder P3000 im selben Netzwerk (WLAN oder LAN)
- Die Zugangsdaten zur Konfigurationsoberfläche des Heizstabs (gibt es bei
  Novolto)

> [!WARNING]
> **Die Novolto-App funktioniert nach der Umstellung nicht mehr.** Sobald
> der Heizstab an deinen eigenen Broker sendet, ist er von der
> Novolto-Cloud getrennt. Gesteuert wird er dann nur noch über Home
> Assistant.

## Schritt 1 – MQTT-Broker in Home Assistant einrichten

Wenn du schon einen MQTT-Broker und die MQTT-Integration nutzt, weiter bei
Schritt 2.

1. **Mosquitto-Broker installieren:** Einstellungen → Add-ons → Add-on-Store
   → „Mosquitto broker“ → Installieren → Starten.
   [Direkt öffnen](https://my.home-assistant.io/redirect/supervisor_addon/?addon=core_mosquitto)
2. **MQTT-Integration einrichten:** Home Assistant schlägt sie danach meist
   selbst vor (Einstellungen → Geräte & Dienste → „Entdeckt“). Sonst:
   [MQTT-Integration hinzufügen](https://my.home-assistant.io/redirect/config_flow_start/?domain=mqtt).
3. **Benutzer für den Heizstab anlegen:** Einstellungen → Personen → Person
   hinzufügen, z. B. `novolto`, die Anmeldung erlauben und ein
   eigenes Passwort vergeben. Das Mosquitto-Add-on nimmt
   Home-Assistant-Benutzer als MQTT-Zugang an.
4. **IP-Adresse von Home Assistant notieren:** Einstellungen → System →
   Netzwerk. Am besten im Router eine feste IP für Home Assistant vergeben,
   sonst verliert der Heizstab die Verbindung, wenn sich die Adresse ändert.

## Schritt 2 – Heizstab mit dem Broker verbinden

1. **Taste am Heizstab 1× kurz drücken.** Der Heizstab öffnet einen
   WLAN-Hotspot.
2. Mit dem Hotspot verbinden und die Konfigurationsoberfläche aufrufen
   (Zugangsdaten gibt es bei Novolto).
3. Vorher die bisherigen MQTT-Einstellungen notieren, damit du zurückkannst.
4. In den **MQTT-Einstellungen** des Heizstabs eintragen (die Feldnamen
   können je nach Firmware etwas abweichen):
   - Adresse des Brokers: IP-Adresse von Home Assistant (aus Schritt 1)
   - Port: `1883`
   - Benutzer und Passwort: der Benutzer aus Schritt 1
   - **Retain einschalten** (empfohlen): Dann kennt der Broker immer den
     letzten Stand, und Home Assistant findet den Heizstab bei der
     Einrichtung sofort. Ohne Retain funktioniert alles genauso, der
     Heizstab wird bei der Einrichtung dann nur gefunden, wenn er gerade
     sendet.
5. Hier steht auch das **MQTT-Topic** des Heizstabs. Ohne Änderung ist es
   die Seriennummer, z. B. `AAA.BBB.123456`. Du brauchst es nur, falls die
   automatische Suche den Heizstab nicht findet.
6. Speichern.

## Schritt 3 – Kommen Daten an?

Optional, hilft aber bei der Fehlersuche: Einstellungen → Geräte & Dienste
→ MQTT → **Konfigurieren** → bei „Auf ein Topic hören“ `+/info` eintragen
→ **Zuhören starten**. Spätestens nach 30 Sekunden erscheint eine Nachricht
mit `"serial"`, `"avtw"` usw. Kommt nichts, siehe
[Fehlersuche](#fehlersuche).

## Schritt 4 – Integration über HACS installieren

[![Repository in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=losnoxos&repository=ha-novolto-mqtt&category=integration)

Oder von Hand: HACS → ⋮ → **Benutzerdefinierte Repositories** →
`https://github.com/losnoxos/ha-novolto-mqtt`, Kategorie **Integration** →
„Novolto MQTT“ herunterladen.

Danach **Home Assistant neu starten** und die Browserseite mit **Strg+F5**
neu laden, sonst taucht die Integration in der Auswahl noch nicht auf.

## Schritt 5 – Heizstab hinzufügen

- **Automatisch:** Meist meldet Home Assistant von selbst „Neues Gerät
  gefunden: Novolto-Heizstab …“ (Einstellungen → Geräte & Dienste).
  Anklicken, Namen vergeben, Modell wählen, fertig.
- **Von Hand:** Einstellungen → Geräte & Dienste → **Integration
  hinzufügen** → „Novolto MQTT“. Die Integration sucht bis zu 35 Sekunden
  nach Heizstäben und zeigt die gefundenen in einer Liste. Taucht deiner
  nicht auf, kannst du das MQTT-Topic selbst eingeben. Die Integration
  prüft dann, ob Daten ankommen, und hilft bei Tippfehlern.

> [!TIP]
> **Wähle den Namen mit Bedacht.** Aus ihm entstehen die Entity-IDs, z. B.
> `water_heater.novolto_p3000`. Ein späteres Umbenennen ändert die IDs
> nicht mit.

Die automatische Erkennung findet Heizstäbe, deren Topic aus einer Ebene
besteht (Normalfall: die Seriennummer). Mehrstufige Topics wie
`haus/heizstab` findet die Suche beim manuellen Hinzufügen.

## Bedienen

- **Temperatur *und* Leistung bestimmen, ob geheizt wird.** Der Heizstab
  heizt nur, wenn die Wassertemperatur unter dem Sollwert liegt *und* die
  Sollleistung über 0 W steht. Steht die Leistung auf 0 W, zeigt der
  Warmwasserbereiter **Aus (0 W)**, egal welche Temperatur eingestellt ist.
- **Schaltdifferenz:** Der Heizstab heizt unter *Soll − Schaltdifferenz/2*
  und stoppt über *Soll + Schaltdifferenz/2*. Beispiel: 60 °C Soll und
  10 °C Schaltdifferenz → heizt unter 55 °C, stoppt über 65 °C. Übliche
  Werte sind 2–5 °C.
- **Zwischenwerte pendeln:** Der P3000 hat drei Heizelemente zu je etwa
  1000 W. Zwischenwerte erreicht er, indem er zwischen den vollen Stufen
  taktet: 1500 W bedeuten im Wechsel 1000 und 2000 W. Das zeigt z. B. eine
  externe Leistungsmessung. Für eine ruhige Netzlast nur volle Stufen nutzen
  (0/1000/2000/3000 W). Die Leistungsstufen-Auswahl ist darauf eingestellt.
- **Maximale Solltemperatur:** Standard 50 °C. Höher geht es nur nach einer
  bestätigten Warnung in den Optionen, wegen Verbrühungsgefahr an der
  Zapfstelle.
- Eingestellte Werte erscheinen sofort im Regler. Lehnt der Heizstab eine
  Einstellung ab, springt der Regler zurück und unter Einstellungen →
  Reparaturen erscheint eine Meldung.

> [!WARNING]
> **Ohne Verbindung heizt der Heizstab weiter.** Nach einem Stromausfall
> startet er mit den zuletzt gesetzten Werten. Fällt Home Assistant oder der
> Broker aus, heizt er mit der letzten Leistung weiter, auch wenn z. B. eine
> PV-Überschussregelung zuletzt 3000 W gesetzt hatte. Diese Automation setzt
> die Leistung nach jedem Start von Home Assistant auf 0 W:
>
> ```yaml
> alias: Novolto nach HA-Start auf 0 W
> triggers:
>   - trigger: homeassistant
>     event: start
> actions:
>   # warten, bis sich der Heizstab wieder meldet
>   - wait_template: >-
>       {{ states('number.novolto_p3000_sollleistung')
>          not in ['unavailable', 'unknown'] }}
>     timeout: "00:05:00"
>     continue_on_timeout: false
>   - action: number.set_value
>     target:
>       entity_id: number.novolto_p3000_sollleistung
>     data:
>       value: 0
> ```

## Dashboard

Beispielkarte (Entity-IDs an deinen Gerätenamen anpassen):

```yaml
type: vertical-stack
cards:
  - type: thermostat
    entity: water_heater.novolto_p3000
  - type: tile
    entity: select.novolto_p3000_leistungsstufe
    features:
      - type: select-options
  - type: entities
    entities:
      - number.novolto_p3000_sollleistung
      - number.novolto_p3000_schaltdifferenz_hysterese
      - sensor.novolto_p3000_leistung
      - binary_sensor.novolto_p3000_heizt
      - sensor.novolto_p3000_energie
```

## Energie-Dashboard

Einstellungen → Dashboards → Energie → **Einzelne Geräte** → Gerät
hinzufügen → Sensor „Energie“ des Heizstabs.

Nicht unter „Netzbezug“ eintragen, das ist für den Hauptzähler. Der Zähler
beginnt bei der Einrichtung bei 0 und übersteht Neustarts. Wird die
Integration gelöscht, ist auch der Zählerstand weg.

## Optionen

Einstellungen → Geräte & Dienste → Novolto MQTT → **Konfigurieren**:

- **Leistungsstufen:** Werte für die Auswahl „Leistungsstufe“, z. B.
  `0, 1000, 2000, 3000`. 0 muss dabei sein. Eine leere Liste blendet die
  Auswahl aus. Zwischenwerte wie 1500 W lässt der Heizstab zwischen den
  vollen Stufen pendeln (siehe [Bedienen](#bedienen)), für eine ruhige
  Netzlast also nur volle Stufen eintragen.
- **Modell:** P2300 oder P3000, legt die maximale Leistung fest.
- **Maximale Solltemperatur:** siehe [Bedienen](#bedienen).
- **Erweiterte Einstellungen** (nur nach Häkchen): Schrittweite der
  Sollleistung, Schwellwert für „Heizt“, minimale Solltemperatur, Bereich
  der Schaltdifferenz, Verfügbarkeits-Timeout, Topic-Namen, Zurücksetzen auf
  Standardwerte.

**MQTT-Topic geändert?** Im Menü der Integration (⋮) → **Neu
konfigurieren**. Gerät, Entitäten und Energiezähler bleiben erhalten.

## Umstieg von manueller MQTT-Konfiguration (YAML)

Wer den Heizstab vorher mit eigenen `mqtt:`-Einträgen in der
`configuration.yaml` eingebunden hatte:

1. Automationen und Energie-Dashboard notieren, die die alten Entitäten
   nutzen.
2. Die `mqtt:`-Einträge für den Novolto aus der YAML entfernen.
3. Entwicklerwerkzeuge → YAML → **Manuell konfigurierte MQTT-Entitäten**
   neu laden.
4. Unter Einstellungen → Geräte & Dienste → Entitäten die verwaisten alten
   Entitäten löschen.
5. Erst dann die Integration hinzufügen. So entstehen keine IDs mit `_2`
   und keine doppelte Energiezählung.

## Mehrere Heizstäbe

Jeder Heizstab wird einzeln hinzugefügt und bekommt eigene Entitäten,
Optionen und einen eigenen Energiezähler. Unterschiedliche Namen vergeben,
z. B. „Heizstab Keller“ und „Heizstab Garage“.

## Fehlersuche

| Symptom | Ursache | Lösung |
|---|---|---|
| „Home Assistant braucht zuerst einen MQTT-Broker …“ | MQTT fehlt | Schritt 1 |
| Suche findet nichts | Heizstab aus, anderer Broker, falscher Benutzer/Passwort | Schritt 2 und 3 prüfen |
| „Keine Daten unter …“ | Tippfehler im Topic (Groß-/Kleinschreibung!) | Topic aus der Konfiguration des Heizstabs übernehmen |
| Alles „Nicht verfügbar“ | Heizstab aus oder keine Netzwerkverbindung | Strom und WLAN/LAN prüfen. Nach dem Neustart von HA dauert es bis zu 30 s |
| Solltemperatur gesetzt, nichts passiert | Sollleistung steht auf 0 W | Leistung erhöhen |
| Integration fehlt in der Auswahl | HA nach HACS-Download nicht neu gestartet | Neustart, Strg+F5 |
| Meldung „hat eine Einstellung abgelehnt“ | Wert vom Heizstab nicht angenommen | Fehlercode in der Meldung, Wert prüfen |
| Meldung „veraltete gespeicherte Nachricht auf dem Broker“ | Retain war im Heizstab an und wurde ausgeschaltet; der Broker hält noch die alte Nachricht | In der Meldung auf **Beheben** klicken und bestätigen, die Nachricht wird gelöscht |

Für eine Fehlermeldung bei GitHub: Einstellungen → Geräte & Dienste →
Novolto MQTT → ⋮ → **Diagnose herunterladen** (die Seriennummer ist darin
unkenntlich gemacht).

## Für Fortgeschrittene

- Topics: `<Topic>/info` (Messwerte alle `msi` Sekunden, dazu Quittungen
  auf Einstellungen) und `<Topic>/control` (Einstellungen `spp`, `sptw`,
  `sptwh`).
- Ein Telegramm, das der Broker als „retained“ ausliefert, zeigt die
  letzten Werte, macht den Heizstab aber nicht verfügbar. Das passiert erst
  mit einer live empfangenen Nachricht.
- Der Status-Sensor zeigt die wichtigste aktive Meldung. Alle aktiven
  Meldungen stehen im Attribut `active`, die Bitmaske in `status_raw`.
- Protokoll-Referenz:
  [NOVOLTO-MQTT.md](https://github.com/losnoxos/dbus-novolto/blob/main/NOVOLTO-MQTT.md)
  in dbus-novolto.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
