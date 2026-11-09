# Änderungsprotokoll

## 0.6.0 · 09.11.2026

Update bestehender Installationen: [Anleitung mit Sicherung und Wiederherstellung](UPDATE.md). Migrationen 0009–0011 laufen beim Backend-Start; vorhandene Konten, FTP-Zuordnungen, Bewertungen und Modelle bleiben erhalten. Vorher Datenbank, Konfiguration und beide Bildbestände sichern. Standardaufbewahrung automatisch gesammelter, unbewerteter Bilder: sieben Tage.

### Behälterreiter im Training

- Training verwendet oberhalb von 700 px je Behälter einen Reiter; bis einschließlich 700 px bleibt die Dropdown-Auswahl. Vorhandene Reiterkomponente wiederverwendet, ohne „Alle“-Reiter im Training.
- Reiter horizontal scrollbar und per Pfeiltasten/Pos1/Ende bedienbar; Auswahl und bestehende Rollenfilter unverändert.

### Laufende Lernsammlung

- Aktuelle Kamerabilder unabhängig von Modellerkennung sammeln und vorhandene Bilder nachholen; FTP-Nachsammlung auch ohne Datumsstruktur.
- Drei-/50-Bilder-Grenzen entfernt; Aufbewahrung je Kamera 1–365 Tage, Standard sieben, mit Vorschau betroffener unbewerteter Bilder. Bestätigte Lernbilder, Uploads und aktuelle Kameravorschau geschützt.
- Automatischer oder manueller Trainingsstart; wartende automatische Jobs beim Moduswechsel abbrechen, laufende Jobs sichtbar beenden. Bestehende Qualitätsprüfung bleibt erhalten.
- Laufende Eingaben bei neuen Bildern erhalten; aktuelle Vorschau, Bewertungsstatus, lokale Aufnahmezeit und getrennte Sammlungs-/Trainingsinformationen.
- Migration 0011 und aktualisierte Regressionstests für den größeren Bildbestand; keine neue Abhängigkeit.

### Ungeeignete Bilder lernen

- Bildprüfung: „Als ungeeignet lernen“ mit Gründen Deckel zu, Sicht verdeckt und Nicht messbar. Bestätigte Gegenbeispiele bleiben mit Bild und ohne Soll-Füllstand erhalten; „Nur verwerfen“ bleibt die dauerhafte Entfernung ohne Training. Bereits gelöschte Dateien werden nicht rekonstruiert.
- Kameraspezifische Prüfung der Bildverwendbarkeit vor der Füllstandsschätzung, getrennt nach Tag/Nacht. Freigabe erst nach unabhängiger Serienvalidierung; ungeeignete oder zweifelhafte Bilder erzeugen keinen Messwert. Automatische Entscheidungen bleiben ohne automatische Lernfreigabe manuell prüfbar.
- Bestätigte Gegenbeispiele korrigieren, sichern, zurücksetzen und wiederherstellen. Bestehende Messwerte bleiben bei nachträglicher Bewertung erhalten. Bestehende Rollen- und CSRF-Regeln gelten auch für die neue Aktion.
- Migration 0010 ergänzt Ablehnungsgrund an Lernbildern und Wiederherstellungssnapshots sowie nullable Soll-Füllstand im Snapshot. Rückbau mit Gegenbeispielen oder daraus trainierten Modellständen gesperrt, damit alte Software diese nicht als Füllstände verwendet. Keine neue Bibliothek.

### Dashboard, FTP-Test und Passwortwiederherstellung

- Dashboard startet mit „Alle“ und behälterbezogenen Reitern. Große Kamerabilder, vorhandene Kennzahlen, Messzeitpunkte und getrennte Kurzverläufe; gemeinsamer Zeitraumfilter.
- Einzelübersicht und Messwertverlauf zusammengeführt. Der eigene Menüpunkt „Verlauf“ entfällt; Messwerttabelle, Raten, Ereignisse und Prüfaktionen bleiben erhalten. Admins erreichen Behälter-, Kamera- und Warnregel-Einstellungen direkt im Behälterkontext.
- FTP-/FTPS-Test in Verbindungs- und Kameradialogen: aktuelle Eingaben prüfen, direkte Ordner und JPG/JPEG/PNG/WEBP-Dateien zählen, ohne Speicherung, Rekursion oder Downloads. Harte 20-Sekunden-Frist und bestehende Eintragsgrenze; sichere Fehler statt falscher Nullzählungen.
- „Passwort vergessen“ mit bestehendem SMTP: kryptografischer Einmalcode per E-Mail, 30 Minuten gültig, Anfragebegrenzung, keine Kontoauskunft und Widerruf aller Sitzungen nach Änderung. Ohne eingerichtetes SMTP klarer Hinweis; CLI-Rücksetzung bleibt verfügbar.
- Migration 0009 ergänzt ausschließlich `password_resets`. Keine neuen Laufzeitabhängigkeiten. Frontend-Tests für begrenzte Parallelität und verworfene Antworten in CI ergänzt.

## 0.5.2 · 01.11.2026

- Füllstandskachel zeigt die Herkunft: „Aktuell“ (automatisch übernommen) oder „Nutzereingabe“ (manuell/Training bestätigt).
- Unsichere, nicht übernommene Auswertung des neuesten Bildes: Füllstand und Inhalt als Schätzung mit gestricheltem Rahmen und „Geschätzt“; Statuszeile mit Bildzeit, Vertrauen und letzter Messung sowie „Jetzt bewerten“ (Training bzw. manuelle Erfassung für Admins). Keine Messwert-/Raten-/Warnwirkung.
- API `/api/containers`: `current_image.analysis` ergänzt.
- Open-Source-Veröffentlichung auf GitHub unter AGPL-3.0-or-later; Quellcode-Link in Fußzeile und Anmeldeseite.
- CI für Backend-Tests (SQLite/PostgreSQL) und Frontend-Typprüfung/Build; Release-Workflow für Multi-Arch-Images (amd64/arm64) in der GitHub Container Registry.
- Synology-Deployment mit fertigen Images (`deploy/synology/compose.yaml`) und Anleitung `docs/SYNOLOGY.md`; englische Kurzfassung `README.en.md`, `CONTRIBUTING.md`, `SECURITY.md`, Issue-/PR-Vorlagen.
- Interne Projektdokumente sind vom Repository und Quell-ZIP ausgeschlossen.
- Weitere bereits im Code enthaltene 0.5.2-Änderungen (u. a. Migration 0008) sind hier noch nicht beschrieben: Noch zu klären.

## 0.5.1 · 01.11.2026

- Automatische zusätzliche FTP-Trainingssammlung aus 168 Stunden, zeitlich verteilt, maximal 50 offene automatische Prüfbilder je Behälter; bestätigte Bilder und Uploads ohne Mengenlimit.
- Zeilenweise begrenzte Datumsordnersuche, persistenter Abruffortschritt und Deduplizierung, höchstens zehn Downloads/30 Sekunden je Kamerazyklus; historische Abrufe ohne Messwert-/Warnwirkung.
- „Verwerfen & weiter“ ersetzt lokales Überspringen: dauerhaft ausschließen, Sammelplatz freigeben und Quelldatei nach Ende abhängiger Trainings bereinigen; Hash-/Herkunftssperre verhindert erneute Sammlung.
- Normale Nutzer prüfen vorhandene Bilder bei eingeschaltetem Lernen einschließlich Bestätigung, Nachbearbeitung und Verwerfen; keine Upload-/Konfigurationsrechte. Serverseitiger Freigabeentzug für Queue, Bildabruf und Änderungen, automatische Ausblendung der Oberfläche.
- Horizontale Dashboard-Warnlinien entfernt; Kachelfarben/Benachrichtigungen erhalten. Lineare Ratenkurven einschließlich sichtbarer Nullstrecken; echte Lücken/Zyklen weiterhin getrennt.
- Migration 0007 mit Erhalt vorhandener Daten und Downgrade-Schutz für verworfene Referenzen; keine neue Laufzeitbibliothek.

## 0.5.0 · 01.11.2026

- Gemeinsamer Bild-für-Bild-Arbeitsplatz mit Upload-/Kamerareferenzen, Lightbox, Bestätigen/Weiter, Überspringen und Nachbearbeitung.
- Kompakter Lernkopf mit Tag-/Nachtzustand, Uploaddialog und ausgelagerten Bereichs-/Detailoptionen; Modell- und Aktivierungsverwaltung entfällt aus der normalen Bedienung.
- Ein gemeinsamer Lernstand mit automatischer Übernahme nach getrennter Qualitätsfreigabe pro Bildmodus; bisherige passende Erkennung bleibt bei Fehlern erhalten.
- Aktuelle Datensnapshots, Erhalt geprüfter Modi und Vermeidung wiederholter Trainings/Rückwechsel.
- Gewünschte Erkennung separat von technischer Nutzbarkeit; wartende automatische Jobs und Übernahmen halten Lernpausen ein.
- Migration 0006 mit Erhalt bestehender Pausen/Versionen und Downgrade-Schutz; keine neuen Laufzeitbibliotheken.
- Direkter Einstieg /training kehrt nach Anmeldung zur Trainingsseite zurück.

## 0.4.2 · 31.10.2026

- System-Refresh fordert einen sofortigen Worker-Durchlauf an und aktualisiert die Daten.
- Dauerhafte Anforderung, Übernahme im Sekundentakt, Zusammenfassung paralleler Klicks und Erhalt unerledigter Anforderungen nach Neustart.
- Durchgehender Arbeitsstatus, gesperrter Startbutton und letzte Abschlusszeit; sichere Rückmeldung bei nicht erreichbarem Dienst.
- Admin-/CSRF-Schutz und Tests für Mehrfachklicks, Wiederanlauf, Fehler und Intervallunterbrechung. Migration 0005; keine neue Laufzeitabhängigkeit.

## 0.4.1 · 31.10.2026

- Dashboard für Nutzer und Admins kompakter: drei kleinere Zahlenkacheln, Bild und Füllstandsverlauf im ersten sichtbaren Bereich; responsiv nebeneinander oder untereinander.
- Messwertdetails/manuelle Erfassung ausklappbar; Ratenverlauf und Ereignisse nachgeordnet.
- Visuelle Stufen: Verbrauch ≤20 % orange/≤5 % rot; Füllen ≥80 % orange/≥95 % rot, zusätzliche Zustandstexte und Diagrammgrenzen.
- Bestehende Benachrichtigungsregeln unverändert; Tablet-Menü und zusätzliche Verdichtung auf niedrigen Mobilbildschirmen.
- Keine neue Migration oder Laufzeitabhängigkeit.

## 0.4.0 · 31.10.2026

- Füllen-/Verbrauchsmodus mit drei Dashboard-Kacheln, Inhalt/Gesamtkapazität und absoluten/prozentualen Raten gemeinsam; Zeitbasis je Behälter.
- Persistierte zeitgewichtete Betriebsraten, konfigurierbares Fenster, versionierte Worker-Neuberechnung und getrennte Ratendiagramm-Zyklen.
- Automatische/manuelle Leerungen und Nachfüllungen; Ereignisse markieren, korrigieren und entfernen ohne künstliche Messungen.
- Füllstandsalarme mit getrennten Schwellen, Hysterese, Alarmphasen und persönlichem Gelesen-Status; Portalhistorie.
- Zentrale verschlüsselte SMTP-Konfiguration, Empfänger je Behälter, persistente Outbox mit Zeitlimit/Wiederholungen und sicherem Status.
- Frei definierbare Polygon-ROI mit Punkteeditor und maskierten Merkmalen; bestehende Rechtecke/Modelle bleiben lesbar.
- Dauerhafte Prüf-Referenzbilder, Thumbnail/Lightbox, bestätigte Bilder als Trainingsdaten und automatische Lernjobs; ausdrückliche Modellaktivierung bleibt.
- Lernstatus und unabhängiger Lernschalter; bei Aus keine neuen sichtbaren Prüfzeilen/-bilder, Inferenz läuft weiter.
- Vorhandene Trainingsserien ergänzen, Datei-Drag-and-drop, bestehendes Appicon als Browser-Favicon.
- Migration 0004, zusätzliche Berechnungs-/Warn-/SMTP-/Trainingsprüfungen; keine neuen Laufzeitbibliotheken.

## 0.3.0 – 31.10.2026

- Admin-Training mit Mehrfach-Uploads, Sollwerten, Serien, Bildmodus und Rechteck-ROI.
- Separates ML-Volume; kein Drei-Bilder-Limit oder automatische Löschung von Trainingsbildern.
- Persistente Worker-Jobs, lokale Random-Forest-JSON-Modelle und gruppenbasierte Validierung.
- Ausdrückliche Modellaktivierung, SHA256 und Kompatibilitätsprüfung von ROI/Ausrichtung.
- Automatische Auswertung; unsichere Ergebnisse ohne KPI-Wirkung zur manuellen Freigabe.
- Rohwerte und geprüfte Werte bleiben nach Produktivbildlöschung erhalten.
- Neue FTP-Batches werden bei laufender Automatik vor Pufferverdrängung analysiert.
- Binärer, begrenzter Multipart-Proxy; andere Schreibanfragen weiterhin 64 KiB.
- Migration 0003, neue lokale Analyse-/Upload-Abhängigkeiten und umfangreiche Trainings-/Gesamttests.


## 0.2.0 – 31.10.2026

- Automatische Erkennung und Speicherung von YYYY/MM/TT-Verzeichnissen, explizite Muster mit TT/DD und rekursive Suche je Kamera.
- Teilmuster MM/TT bzw. TT bei bereits auf Jahr oder Monat gesetztem FTP-Root; Strukturerkennung ohne Durchlaufen alter Bildordner und mit Zeitraumprüfung des Roots.
- Konfigurierbares Tagesfenster und regelmäßige Kontrollscans mit Monats-/Jahreswechsel und Rückfall auf bekannte Ablagen.
- Neueste Bilder zuerst; ältere Archivbilder verdrängen keinen aktuellen Drei-Bilder-Puffer.
- Sichere Fehlercodes mit differenzierten Hinweisen für Netzwerk, Anmeldung, TLS, Pfade/Rechte, Scanlimits, Bilddateien und lokalen Speicher.
- Echte Worker-Arbeitsphasen und Fortschrittszähler, unabhängiges Lebenszeichen sowie animiertes Icon im Portal und Systemstatus.
- Statusabfrage alle zwei Sekunden für Admins, ohne Verlauf/Stammdaten erneut zu laden.
- Additive Alembic-Migration 0002 für Kamera-Suchkonfiguration, Scanstatistik und Worker-Aktivität.
- Automatisierte FTP-, Datumswechsel-, Fehler-, Kontrollscan- und Statusprüfungen ergänzt.


## 0.1.0 – 31.10.2026

- Milestone 1–3 als neues Portal unter `0_Product/repo` umgesetzt.
- Docker Compose mit Frontend, FastAPI, separatem Import-Worker und PostgreSQL 17; Healthchecks und Alembic-Migration.
- Argon2-Anmeldung, gehashte Sessions, CSRF-Schutz, Loginlimit, Admin/User, Benutzeranlage und Kontodeaktivierung mit Sitzungswiderruf.
- Standorte und allgemeine Behälter mit Kapazität, Einheit, Beschreibung, Aktivstatus und Kamera konfigurierbar.
- Verschlüsselte FTP-Zugänge, FTP und explizites FTPS, rekursive MLSD-Scans mit NLST-Fallback.
- Größen-/Pixelgrenzen, Upload-Mindestalter, Transfervergleich, SHA256-Deduplizierung, normalisierte Bilder und EXIF-Zeitpriorität.
- Höchstens drei lokale Produktivbilder je Kamera, automatische Löschung und Absturzbereinigung; dauerhaftes Metadatenjournal ohne Bildbytes.
- Geschütztes aktuelles Bild, manuelle Füllstandserfassung, absolute Menge, Restkapazität, dauerhafte Historie und gespeicherte Änderungsraten.
- Kapazitäts-/Einheitssnapshots erhalten historische Mengen; verspätete Eingaben berechnen Folgeraten neu.
- Responsive Portaloberfläche mit Dashboard, Anlagen, Verlauf, Benutzer- und Systemansicht. Unterverzeichnis über `APP_BASE_PATH` mit passendem Cookie-Pfad.
- Backend-Tests einschließlich echtem FTP-Testserver, PostgreSQL-Migrationsprüfung und HTTP-Gesamttest über Next.js/API/Worker erstellt und ausgeführt.
- Planprüfung, Architektur, ML-Fortsetzung, Software-Spezifikation, Asset-Memo und Betriebsanleitung angelegt.

Noch ausstehend: Milestone 4–7, Docker-Abnahme auf einem Docker-fähigen Host, visuelle Browserprüfung, reale Kamera-/FTP-Abnahme und Backup-Wiederherstellung.
