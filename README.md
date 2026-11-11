<p align="center">
  <img src="docs/images/logo.svg" alt="fillens" width="240">
</p>

<p align="center"><strong>Weniger schätzen. Mehr wissen.</strong><br>Selbst gehostetes Füllstandsportal für Behälter, Tanks und Papierkörbe – mit Kamerabildern, lokalem Lernen und Warnungen.</p>

<p align="center">
  <a href="https://github.com/mumpelmat/fillens/actions/workflows/ci.yml"><img src="https://github.com/mumpelmat/fillens/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/mumpelmat/fillens/tags"><img src="https://img.shields.io/github/v/tag/mumpelmat/fillens?label=Version&color=1e634d" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Lizenz-AGPL--3.0-1e634d" alt="Lizenz AGPL-3.0"></a>
  <img src="https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-1e634d?logo=docker&logoColor=white" alt="Docker amd64 | arm64">
</p>

<p align="center">
  <a href="README.en.md">English</a> ·
  <a href="docs/SYNOLOGY.md">Installation auf Synology</a> ·
  <a href="#schnellstart-mit-docker">Schnellstart</a> ·
  <a href="CONTRIBUTING.md">Mitwirken</a>
</p>

![Übersicht mit Füllstand, aktuellem Kamerabild und Verlauf](docs/images/uebersicht.png)

## Was Fillens macht

Kameras legen Bilder per FTP ab. Fillens importiert sie, erfasst Füllstände manuell oder per lokal trainierter Bilderkennung, berechnet Füll- und Verbrauchsraten und warnt im Portal und per E-Mail. Alles läuft auf eigener Hardware, empfohlen als Docker-Installation z. B. auf einer Synology; es gibt keinen Cloud-Dienst.

- **FTP-/FTPS-Import** mit Datumsordnersuche, Deduplizierung und Aufbewahrung je Kamera (standardmäßig sieben Tage, kein Bildanzahllimit)
- **Füllen- oder Verbrauchsmodus** mit geglätteten Raten, Ereignissen (Leerung, Nachfüllung) und Verlaufsdiagrammen
- **Lokales Lernen:** Bildbereich als Vieleck markieren, Bilder bestätigen, getrennte Lernstände für Tag und Nacht; neue Lernstände werden erst nach bestandener Prüfung verwendet. Trainingsbilder haben **kein Drei-Bilder-Limit**.
- **Warnungen** mit Schwellen und Hysterese im Portal und per SMTP
- **Füllstand korrigieren** direkt unter dem Kamerabild im Dashboard; die Korrektur wird Messwert und Lernbeispiel zugleich
- **Rollen** Administrator/Benutzer mit Kontoverwaltung (Rolle ändern, Passwort setzen, löschen), Argon2-Passwörter, CSRF-Schutz, verschlüsselte FTP-/SMTP-Zugangsdaten

<table>
  <tr>
    <td width="50%"><img src="docs/images/training.png" alt="Bilder bestätigen und lernen"></td>
    <td width="50%"><img src="docs/images/anlagen.png" alt="Anlagen und Behälter mit Warnregeln"></td>
  </tr>
  <tr>
    <td align="center"><sub>Bilder bestätigen &amp; lernen</sub></td>
    <td align="center"><sub>Anlagen, Behälter und Warnregeln</sub></td>
  </tr>
</table>

Version **0.7.0**, Stand **11.11.2026**. Änderungen im [Änderungsprotokoll](docs/CHANGELOG.md).

## Schnellstart mit Docker

Benötigt werden Docker Engine/Desktop mit Compose v2 und Python 3 für die einmalige Konfiguration. Im Wurzelverzeichnis des Repositorys:

```bash
python3 scripts/init_env.py --http
docker compose up -d --build
docker compose exec backend python -m app.cli create-admin --email admin@firma.de
```

Das Passwort wird verdeckt abgefragt, mindestens zwölf Zeichen. Danach `http://localhost:3000` öffnen. Es gibt keine voreingestellten Benutzer oder Standardpasswörter. `--http` erlaubt Cookies bei einem lokalen HTTP-Test; für HTTPS die Konfiguration ohne dieses Argument erstellen bzw. `COOKIE_SECURE=true` setzen. Eine vorhandene `.env` wird vom Skript niemals überschrieben.

Nach dem ersten Build reicht `docker compose up -d`. PostgreSQL wird zuerst geprüft, anschließend führt das Backend die Migrationen aus. Worker und Frontend starten erst nach erfolgreicher API-Gesundheitsprüfung.

## Installation auf Synology

Für den Dauerbetrieb auf einer Synology (DSM 7.2+, Container Manager) gibt es fertige Images für amd64 und arm64 in der GitHub Container Registry (`ghcr.io/mumpelmat/fillens-backend`, `ghcr.io/mumpelmat/fillens-frontend`). Es wird nichts auf dem NAS gebaut. Compose-Datei: [deploy/synology/compose.yaml](deploy/synology/compose.yaml), Schritt-für-Schritt-Anleitung: [docs/SYNOLOGY.md](docs/SYNOLOGY.md).

## Erster Papierkorb

1. Als Administrator anmelden und **Behälter anlegen** wählen. Beim ersten Behälter einen Standort anlegen, Kapazität z. B. **30 Liter**.
2. Unter **System → FTP-Verbindung** Server, Port, Zugang und Root eintragen. FTP und explizites FTPS werden unterstützt.
3. Unter **Anlagen → Kamera** die Verbindung zuweisen. Ein optionales Kamera-Unterverzeichnis ist relativ zum FTP-Root. Die Kamera-Zeitzone bestimmt die Interpretation von EXIF-Zeitstempeln.
4. Der Worker durchsucht das Verzeichnis einschließlich beliebiger Unterverzeichnisse. Nach dem nächsten Scan erscheint das aktuelle Bild.
5. Im **Dashboard** unter dem Kamerabild **Füllstand korrigieren** wählen und den tatsächlichen Füllstand von 0 bis 100 % eintragen. Der Wert gilt für das neueste Bild; ist inzwischen ein neueres Bild eingetroffen, bittet das Portal um Aktualisierung.
6. Drei KPI-Karten zeigen Prozentfüllstand, Inhalt/Gesamtkapazität sowie absolute und prozentuale Betriebsrate gemeinsam. Unter **Anlagen → Bearbeiten** den Modus **Füllen** oder **Verbrauch**, die Zeitbasis und das Glättungsfenster wählen. Nach der Worker-Berechnung stehen Füllstands- und Ratendiagramm unter **Verlauf** bereit.
7. Unter **Benutzer** weitere Konten mit Leserechten oder Administratorrechten anlegen. Dort lassen sich auch Rollen ändern, neue Passwörter setzen und Konten löschen. Benutzer sehen Übersicht, Anlagen, Verlauf und aktuelles Bild; sie können nichts konfigurieren, jedoch eigene Benachrichtigungen als gelesen markieren und bei Behältern mit eingeschaltetem Lernen den Füllstand korrigieren.

Pro importiertem Produktivbild ist ein effektiver Messwert möglich. Unsichere Modellergebnisse werden zunächst getrennt zur Prüfung gespeichert. Eine manuelle Freigabe erhält den Modell-Rohwert. Änderungen bereits übernommener Messungen mit vollständigem Korrekturprotokoll gehören zum späteren Milestone 6. Aktuelle Werte und Bilder werden alle 30 Sekunden neu geladen.

## Bilder bestätigen und lernen

1. Als Admin **Training** öffnen (direkt auch `/training`) und Behälter wählen. Es gibt einen sichtbaren Lernstand für Tag und Nacht, keine Modellverwaltung im normalen Ablauf.
2. **Bilder hinzufügen** öffnen oder Dateien auf die Uploadfläche ziehen. Tag/Nacht wird automatisch erkannt. Vorhandene Aufnahmegruppen und ein gemeinsamer tatsächlicher Füllstand können im Uploaddialog optional angegeben werden.
3. Den Behälterbereich einmal über **Bildbereich festlegen** als Vieleck markieren. Später über **Bildbereich bearbeiten** ändern. Unpassende alte Erkennung wird bis zum neuen passenden Lernstand pausiert.
4. In **Zu prüfen** ein Bild groß ansehen, bei Bedarf per Lightbox vergrößern, den tatsächlichen Füllstand eingeben und **Bestätigen & weiter** wählen. Uploads und automatische Referenzen stehen in derselben Liste. **Verwerfen & weiter** schließt das offene Bild dauerhaft aus und schafft Platz für weitere Referenzen.
5. Unter **Bestätigt** lassen sich Trainingsvorgaben nachbearbeiten. Tag-/Nachtzuordnung und Aufnahmegruppe stehen unter **Bilddetails**. Bei offenen Kameraergebnissen übernimmt eine Bestätigung zugleich den geprüften Messwert; bereits übernommene Messwerte werden durch reine Trainingskorrektur nicht nachträglich geändert.
6. Der Worker trainiert und prüft neue Lernstände im Hintergrund und verwendet geeignete Verbesserungen automatisch. Pro Bildmodus sind zwölf bestätigte Bilder, drei Füllstände, 40 Prozentpunkte Spannweite und zwei unabhängige Aufnahmegruppen nötig; die Validierung muss mindestens zwei zurückgehaltene Bilder und höchstens zehn Prozentpunkte mittleren Fehler erreichen. **Tag bereit / Für Nacht noch Bilder bestätigen** ist ein normaler Zwischenstand.
7. **Weiter lernen** aus stoppt neue Prüfbilder, neue automatische Trainings und automatische Übernahmen. Die bestehende Erkennung bleibt unabhängig nutzbar; laufende Trainings dürfen fertig werden. Gespeicherte Bilder und explizite Bestätigungen bleiben erhalten.
8. Unter **Weitere Einstellungen** kann die automatische Erkennung separat pausiert und das Mindestvertrauen geändert werden. Eine zuvor pausierte Erkennung bleibt beim Upgrade pausiert und lässt sich über **Erkennung fortsetzen** einschalten.

Kamera- und Lernbilder haben kein festes Anzahl-Limit. Unbewertete automatische Bilder bleiben standardmäßig sieben Tage ab lokaler Aufnahme erhalten; Admins können 1–365 Tage je Kamera wählen. Die neueste Kameravorschau, bestätigte Lernbilder und manuelle Uploads bleiben erhalten. Menschliche Bewertungen dienen als Trainingsvorgaben; Modellschätzungen bestätigen sich nicht selbst.

Ein neuer Lernstand ersetzt den bisherigen erst nach bestandener Prüfung, mit aktueller Datenbasis und passendem Bildbereich. Fehler oder nicht ausreichende Daten erhalten die bisherige passende Erkennung. Bereits geprüfte Tages-/Nachtfreigaben werden durch einen Wechsel nicht verloren. Bestehende Versionen bleiben intern als Historie erhalten.

Der Qualitätsstatus ist keine Genauigkeitsgarantie für reale Einzelbilder. Sinnvoll sind unterschiedliche tatsächliche Füllstände, Materialien und Beleuchtungen aus getrennten Aufnahmerunden. Nach geänderter Kameradrehung/Spiegelung müssen passend ausgerichtete Bilder neu hinzugefügt werden. Ein vollständiges Korrektur-Audit übernommener Messwerte bleibt ein weiterer Schritt. Technische Details und Grenzen in [ML.md](ML.md).

## Worker sofort ausführen

Unter **System** löst der Refresh-Button zusätzlich einen sofortigen Worker-Durchlauf aus. Der gestartete Worker übernimmt die Anforderung innerhalb etwa einer Sekunde, unabhängig vom regulären Scanintervall. Läuft er bereits oder wartet eine Anforderung, entsteht kein zweiter Durchlauf. Button/Arbeitsstatus zeigen den Zustand; die letzte Abschlusszeit steht in der Worker-Karte. Der vollständige Ablauf umfasst die normalen anstehenden Trainings-, Import-/Analyse-, Raten- und Versandaufgaben.

Bei „Worker nicht erreichbar“ den Dienst über `docker compose up -d worker` starten. Der Portalbutton startet keinen gestoppten Container. Andere Refresh-Buttons und automatische Anzeigeaktualisierungen fordern keine Worker-Arbeit an. Manuelle Starts gelten für Admins und benötigen den normalen CSRF-Schutz.

## Kompakte Übersicht

Drei kleine Zahlenkacheln, Kamerabild und Füllstandsverlauf stehen im Vordergrund. Auf Laptop/Desktop werden Bild und Verlauf nebeneinander, auf dem Handy darunter angezeigt. **Messwertdetails** lassen sich aufklappen; Admins finden dort auch die manuelle Erfassung. Ratenverlauf und Ereignisse stehen weiter unten.

Die Prozent-/Inhaltskacheln werden im Modus Verbrauch bei **≤20 % orange** und **≤5 % rot**; beim Füllen ab **80 % orange** und ab **95 % rot**. Die Grenzen erscheinen auch im Füllstandsdiagramm. Diese festen Anzeigefarben ändern die getrennt konfigurierbaren Benachrichtigungsregeln nicht.

## Füllen, Verbrauch und Warnungen

Unter **Anlagen → Bearbeiten** Modus, Zeitbasis (Minute/Stunde/Tag), Glättungsfenster (1 Minute bis 7 Tage; Standard 60 Minuten) und Gegenbewegungsgrenzwert (Standard 20 Prozentpunkte) einstellen. Füllen zählt Zunahmen, Verbrauch zählt Abnahmen. Die Ratenkachel zeigt Menge und Prozentpunkte der Gesamtkapazität gemeinsam. Beispiel: 60 → 55 % in einer Stunde entspricht fünf Prozentpunkten Verbrauch pro Stunde. Kleine Gegenbewegungen werden mit Vorzeichen geglättet, ein insgesamt gegenläufiges Ergebnis wird null.

Leerungen/Nachfüllungen unterbrechen Betriebszyklen. Admins können unter Dashboard/Verlauf Ereignisse markieren, korrigieren oder entfernen, ohne den Füllstand zu verändern. Nach einer manuellen Markierung wird der erste folgende Messwert zur neuen Basis. Neuberechnung erfolgt im Worker; bis dahin steht dort **Wird neu berechnet**. Datenlücken über dem Fenster und Kapazitäts-/Einheitenwechsel beginnen ebenfalls eine neue Basis. Das Ratendiagramm verbindet keine verschiedenen Zyklen.

Unter **Anlagen → Warnregeln** den Behälter auswählen und getrennte Schwellen einstellen: Füllen standardmäßig Hinweis ab 80 %/Warnung ab 90 %, Verbrauch Hinweis bis 20 %/Warnung bis 10 %. Zwei Prozentpunkte Hysterese und einmalige Meldungen pro Stufe/Alarmphase verhindern Grenzwertflattern. Ein direkter Sprung zur Warnung erzeugt beide Portalstufen, jedoch nur eine E-Mail pro Empfänger. **Meldungen** zeigt aktive Meldungen, Historie und persönlichen Gelesen-Status. Keine Restzeitprognose.

Für E-Mail unter **System → E-Mail-Versand (SMTP)** Server, Port, Verschlüsselung und Absender eintragen, bei Bedarf Benutzer/Passwort. Ein leeres Passwort behält das vorhandene; eine eigene Option entfernt es. Danach Empfänger unter den Behälter-Warnregeln hinterlegen und Versand aktivieren. Ohne vollständige SMTP-Konfiguration funktionieren Portalwarnungen weiter. Der Worker versendet aus der Outbox mit bis zu fünf Versuchen; Admins sehen Versand-/Fehlerstatus bei der jeweiligen Meldung. Warnmails werden beim tatsächlichen Worker-Versand verschickt; angeforderte Passwort-Rücksetzcodes werden unabhängig davon über eine begrenzte Hintergrundaufgabe gesendet. Bei Abbruch zwischen Zustellung und DB-Bestätigung ist eine doppelte Zustellung möglich.

## Konfiguration

Die Datei `.env` ist lokal, mit Rechten `0600`, und darf nicht ins Repository oder Release-Paket gelangen.

| Variable | Bedeutung / Vorgabe |
|---|---|
| `POSTGRES_PASSWORD` | Zufällig erzeugtes Datenbankpasswort; vom Skript als Hexwert erzeugt |
| `ENCRYPTION_KEY` | Fernet-Schlüssel zur Verschlüsselung der FTP-Passwörter; dauerhaft sicher aufbewahren |
| `APP_BASE_PATH` | Leer für die Domainwurzel, sonst z. B. `/fuellstand`; ohne abschließenden Schrägstrich |
| `COOKIE_SECURE` | `true` für HTTPS; für lokalen HTTP-Test `false` |
| `SESSION_HOURS` | Sitzungsdauer, Standard 12 Stunden |
| `BIND_ADDRESS` | Standard `127.0.0.1`; für direkten LAN-Zugriff bewusst auf `0.0.0.0` ändern |
| `ML_DATA_DIR` | Interner Pfad des separaten Trainings-/Modellspeichers, Compose: `/ml` |
| `PORT` | Öffentlicher Frontend-Port, Standard 3000 |
| `FTP_POLL_SECONDS` | Scanintervall, Standard 60 Sekunden |
| `FTP_SETTLE_SECONDS` | Mindestalter einer Datei vor Download, Standard 30 Sekunden |
| `FTP_TIMEOUT_SECONDS` | Timeout je FTP-Netzwerkoperation, Standard 20 Sekunden |
| `MAX_IMAGE_BYTES` | Grenze je Download, Standard 15 MiB |
| `MAX_SCAN_FILES` | Maximale Anzahl Verzeichniseinträge je Scan, Standard 10.000 |
| `MAX_IMPORTS_PER_CYCLE` | Maximale neue Downloadversuche je Kamera und Zyklus, Standard 100 |

Die API verwendet außerdem `DATABASE_URL`, `DATA_DIR` und das Frontend `BACKEND_INTERNAL_URL`. Compose setzt diese intern. Die Zugangsdaten dürfen keine unkodierten URL-Sonderzeichen im Datenbank-URL enthalten; das Initialisierungsskript erzeugt deshalb ein Hexpasswort.

## Domain, Unterverzeichnis und Reverse Proxy

Die öffentliche Domain ist nicht im Code hinterlegt. API und Bilder werden über denselben Ursprung wie die Oberfläche geladen. Bei einer Installation unter einem Unterverzeichnis:

```bash
python3 scripts/init_env.py --base-path /fuellstand
docker compose up -d --build
```

Bei bestehender `.env` dort `APP_BASE_PATH=/fuellstand` setzen und `docker compose up -d --build` ausführen. **Next.js bindet den Basis-Pfad beim Build ein**; eine spätere Pfadänderung benötigt einen Frontend-Neubau. Ein Domainwechsel allein benötigt keinen Neubau. Backend-Cookie-Pfad und Frontend-Basis-Pfad müssen übereinstimmen.

Beispiel für einen bereits TLS-terminierenden Nginx-Server:

```nginx
location ~ ^/fuellstand(?:/|$) {
    proxy_pass http://127.0.0.1:3000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    client_max_body_size 64k;
}
```

Der Präfix bleibt beim Weiterreichen erhalten. Bei Betrieb an der Domainwurzel entsprechend `location /` verwenden. Es sind nur Frontend-Port und TLS-Proxy öffentlich erreichbar; API und Datenbank erhalten in Compose keine veröffentlichten Ports. Eine konfigurierte FTP-Adresse kann bewusst auch im privaten Kameranetz liegen. FTP-Zugang ausschließlich Administratoren überlassen.

## Bildpuffer und Import

### Effiziente Suche und Worker-Anzeige (ab 0.2.0)

Unter **Anlagen → Kamera → Ordnerstruktur** stehen drei Suchmodi zur Verfügung:

- **Automatisch:** Erkennt `YYYY/MM/TT` auch unter festen Präfixordnern (bis drei Präfixebenen), speichert das Muster und verwendet es in den Folgescans. Bei mehreren unterschiedlichen Datumsbäumen oder unbekannter Struktur bleibt die Suche rekursiv. Dann den Kamera-Root enger setzen oder ein Muster vorgeben.
- **Datumsmuster:** Beispielsweise `YYYY/MM/TT`, `YYYY/MM/DD` oder `bilder/YYYY/MM/TT/events`. Der Pfad ist relativ zum kombinierten FTP-Root/Kamera-Unterverzeichnis. DD wird als TT normalisiert.
- **Rekursiv:** Durchsucht bei jedem Zyklus den gesamten konfigurierten Baum.

Im Datumsmodus werden standardmäßig heute und die zwei vorherigen Tage in der Kamera-Zeitzone geprüft. Ein zuletzt bekannter Tagesordner dient als Rückfall, wenn noch keine aktuelle Ablage vorhanden ist. Monats- und Jahreswechsel benötigen keine neue Konfiguration. Nach standardmäßig 24 Stunden folgt zusätzlich ein Struktur-Kontrollscan für verspätete Ablagen; Intervall und Tagesfenster sind pro Kamera konfigurierbar. Der erste Datumsimport wartet nicht auf das Durchlaufen des gesamten Bildarchivs. Änderungen an Root, Verbindung oder Muster verwerfen die gespeicherte Erkennung automatisch.

Zeigt der Root bereits auf ein Jahr (z. B. `/2026`), erkennt die Automatik stattdessen **`MM/TT`**; bei `/2026/10` erkennt sie **`TT`**. Auch diese Teilmuster können ausdrücklich vorgegeben werden. Dabei werden alte Bildordner zur Erkennung nicht geöffnet: Monats-/Tagesnamen reichen aus. Ein fest eingestellter Jahres-/Monatsroot bleibt eine Zugriffsgrenze. Beim Wechsel aus diesem Zeitraum erscheint `date_root_outdated`; für automatischen Jahreswechsel den Root auf die darüberliegende Ebene setzen und `YYYY/MM/TT` verwenden. Die Anwendung wechselt nicht stillschweigend außerhalb des konfigurierten Roots.

Auch der Kontrollscan folgt der Datumsstruktur: Er liest Jahres-/Monats-/Tagesverzeichnisse und prüft Bildlisten nur in seit dem letzten Kontrolllauf geänderten Tagesordnern. Zusätzlich werden drei alte Tagesordner rotierend als Stichprobe geprüft, falls ein Server Ordnerzeitstempel nicht zuverlässig aktualisiert. Ein komplettes Durchlaufen aller historischen Bildlisten findet im Datumsmodus nicht statt. Ohne zuverlässige Ordnerzeitstempel können spät ersetzte Dateien außerhalb des Tagesfensters daher erst bei einer späteren Stichprobe auffallen.

Neue Dateien werden nach FTP-Zeitstempel absteigend geprüft. Auch verspätet eintreffende Dateien innerhalb der eingestellten Aufbewahrungsdauer werden importiert. Ältere Archivdateien werden übersprungen, sofern sie nicht die neueste verfügbare Vorschau einer länger stillen Kamera verbessern. Aufnahmezeit und lokale Importzeit werden getrennt gespeichert.

Das **Worker-Symbol oben im Portal** und die **Import-Worker-Karte unter System** drehen sich während tatsächlicher Arbeit: Verbindung, Erkennung, Suche, Kontrollscan, Download oder Speicherung. Der Admin-Status aktualisiert sich alle zwei Sekunden, die eigentlichen Portal-/Bilddaten weiterhin alle 30 Sekunden. Kurze Vorgänge zwischen zwei Statusabfragen können unbeobachtet bleiben. Ohne frisches Lebenszeichen wird keine Arbeitsanimation angezeigt. Ein separates Lebenszeichen alle fünf Sekunden hält lange Scans als erreichbar.

Unter **System → Kameras & Bildpuffer** erscheinen Suchart, erkanntes Muster, geprüfte Einträge und differenzierte Fehler mit technischem Fehlercode. Ein Fehler des Kontrollscans verwirft bereits erfolgreich importierte aktuelle Bilder nicht; der nächste Kontrollversuch erfolgt nach dem eingestellten Intervall.

| Fehlercode | Bedeutung / Prüfung |
|---|---|
| `authentication` | Anmeldung abgelehnt; Benutzername, Passwort, Freigabe |
| `dns`, `connection_refused`, `timeout`, `network` | Namensauflösung, Port, Dienst oder Netzwerk |
| `tls_certificate`, `tls` | FTPS-Zertifikat oder Verschlüsselung |
| `root_unavailable`, `directory_unavailable` | Pfad fehlt oder Zugriff verweigert; FTP-550 unterscheidet dies nicht eindeutig |
| `date_directories_unavailable` | Keine zugänglichen Ordner passend zum Datumsmuster |
| `date_root_context`, `date_root_outdated` | Teilmuster passt nicht zum Root oder Root ist auf einen vergangenen Zeitraum begrenzt |
| `data_connection` | Passive Datenports/Firewall oder FTPS-Datenkanal |
| `scan_limit`, `depth_limit`, `discovery_limit` | Suchumfang zu groß; Root/Muster bzw. Limits anpassen |
| `image_size`, `image_invalid`, `image_incomplete` | Bildgröße, beschädigtes Format oder unvollständiger Transfer |
| `password_decryption` | Verschlüsselungsschlüssel stimmt nicht mehr zum gespeicherten Passwort |
| `storage`, `storage_full` | Lokale Volume-Rechte oder freier Speicher |

Rohe Serverantworten werden weder angezeigt noch protokolliert. Ein manueller Neustart ist zum Wechsel des Suchmodus nicht nötig; die Einstellung gilt ab dem nächsten Kamerazyklus.

- JPEG, PNG und WebP; tatsächliches Format wird validiert. Maximal 25 Millionen Pixel und konfigurierbares Downloadlimit. Lokale Dateien werden als JPEG ohne EXIF-Daten abgelegt.
- Rekursiver Scan über MLSD; für ältere Server NLST mit CWD/SIZE/MDTM. Keine feste Datums- oder Verzeichnisstruktur. Maximale Tiefe: 32 Unterverzeichnisse.
- Neue Dateiversionen werden anhand von Pfad, Größe und FTP-Zeitstempel erkannt. Nach dem Download prüft SHA256 auf bereits bekannte Inhalte, auch nach der Bildlöschung.
- Größe und Zeitstempel werden vor und nach dem Transfer verglichen. Bei fehlendem FTP-Zeitstempel wird zunächst die konfigurierbare Wartezeit beobachtet. Ein Server, der Inhalte ohne Änderung von Größe/Zeitstempel ersetzt, kann über diese Metadaten nicht zuverlässig erkannt werden.
- Zeitpriorität: EXIF `DateTimeOriginal`, FTP-Zeitstempel in UTC, Importzeit. Ein Dateinamen-Parser ist laut Plan optional und noch nicht enthalten. EXIF ohne Offset verwendet die konfigurierte Kamera-Zeitzone; bei doppeldeutiger Winterzeit gilt `fold=0`.
- Keine Begrenzung auf drei oder 50 Bilder. Die Aufbewahrungsdauer je Kamera steuert die Bereinigung automatisch gesammelter, unbestätigter Referenzen; die neueste Aufnahme bleibt sichtbar.
- Downloads werden begrenzt im Arbeitsspeicher validiert. Es entsteht keine zusätzliche temporäre Bildhistorie. Ein Worker besitzt Datei- und PostgreSQL-Sperre. Beim nächsten Scan werden verwaiste Dateien/Datensätze bereinigt.
- Das dauerhafte Importjournal enthält nur Metadaten, Hash und Status. Messwerte verweisen darauf; sie besitzen keinen löschabhängigen Fremdschlüssel auf den Bildpuffer.
- Der Import ist **lesend gegenüber dem FTP-Server**. Die lokale Aufbewahrung ist unabhängig von der Aufbewahrung auf Kamera/FTP-Server.
- Keine externen KI-Dienste. Bestätigte positive/negative Lernbilder und manuelle Uploads bleiben im separaten Trainingsspeicher dauerhaft erhalten; unbewertete automatische Kopien unterliegen der eingestellten Aufbewahrung. Proxy/CDN darf Bild- und API-Antworten mit `Cache-Control: no-store` nicht archivieren.

## Dashboard und Messungen

**Alle** zeigt die Behälter gemeinsam mit großen Kamerabildern und getrennten Kurzverläufen. Die Zeitraumwahl gilt für alle Diagramme. Ein Behälter-Reiter oder **Messungen ansehen** öffnet Bild, Kennzahlen, Verlauf, Messwerttabelle und Ereignisse in einer gemeinsamen Ansicht. Administratoren erreichen über **Einstellungen** direkt die bestehenden Behälter-, Kamera- und Warnregel-Einstellungen.

Unter jedem aktuellen Kamerabild steht der erkannte oder geschätzte Füllstand mit **Füllstand korrigieren**. Die Eingabe wird zum Messwert des neuesten Bildes – auch wenn das Modell bereits einen Wert übernommen hat – und zugleich als bestätigtes Lernbild gespeichert. Raten werden neu berechnet. Es ist die einzige Bewertungsaktion im Dashboard; die Lernliste wird weiterhin unter **Training** bearbeitet. Admins dürfen immer korrigieren, Benutzer nur bei Behältern mit eingeschaltetem Lernen.

## Betrieb, Migrationen und Fehlerdiagnose

```bash
docker compose ps
docker compose logs --tail=100 backend worker frontend
docker compose exec backend alembic current
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.cli reset-password --email admin@firma.de
```

Auf der Anmeldung führt **Passwort vergessen?** bei eingerichtetem SMTP zu einer E-Mail mit einem Einmalcode. Der Code gilt 30 Minuten und wird unter **Ich habe bereits einen Code** zusammen mit dem neuen Passwort eingegeben. Auch rein lokale Installationen benötigen dafür keine öffentliche URL. Ohne SMTP bleibt der oben gezeigte CLI-Befehl nutzbar. Nach einem Versandfehler oder Serverneustart gegebenenfalls erneut anfordern; keine automatische Wiederholung von Rücksetzmails.

Ein Passwortreset widerruft alle Sitzungen dieses Benutzers. Kontodeaktivierung beendet ebenfalls alle Sitzungen. Setzt ein Admin unter **Benutzer** ein neues Passwort, werden offene Rücksetzcodes und alle Sitzungen dieses Kontos beendet. Rollenwechsel gelten sofort ohne Neuanmeldung. Gelöschte Konten verlieren nur den Personenbezug; Messwerte, Lernbilder und Modelle bleiben erhalten. Das eigene Administratorkonto kann im Portal weder deaktiviert, herabgestuft noch gelöscht werden. Anmeldung ist nach fünf Versuchen je Konto in 15 Minuten gesperrt; es gibt keine offene Registrierung.

System zeigt Arbeitsphase und Worker-Lebenszeichen, letzte Scans/Importe, Suchstrategie, Fehlercodes und Puffergröße. Ein aktuelles Lebenszeichen bestätigt den Worker, keine frischen Kamerabilder: Technische Frische-/Kamerawarnungen bleiben ein weiterer Schritt; Füllstandsbenachrichtigungen sind umgesetzt. In den FTP-Einstellungen prüft **Verbindung testen** die aktuellen Zugangsdaten und den Root. Im Kameradialog prüft **FTP-Ordner testen** zusätzlich das Kamera-Unterverzeichnis. Beide Tests zeigen Ordner und Bilddateien ausschließlich auf erster Ebene, ohne etwas zu speichern oder Bilder herunterzuladen. Grenzen und Fehler werden ausdrücklich gemeldet. Der reguläre Import prüft die zugewiesenen Verbindungen weiterhin im Worker. Fehlertexte sind bewusst ohne Zugangsdaten; Logs enthalten Ereignistyp, Kamera-ID, sicheren Fehlercode und Fehlerklasse.

Ein einzelner Worker ist vorgesehen. Ein zweiter wird abgewiesen. Ein Scan erfolgt pro Kamera nacheinander; bei vielen Kameras/Dateien verlängert sich der Zyklus. Den FTP-Root möglichst eng wählen und Suchmodus/Scanlimits passend setzen. FTP-Server sollten nur lesbare Kameraverzeichnisse freigeben; Symlinks vermeiden. Das Lebenszeichen läuft unabhängig vom Scan; Netzwerkfehler der Datenbank können es dennoch unterbrechen.

## Backup und Update

Für bestehende Installationen gilt die [Update-Anleitung für Version 0.7.0](docs/UPDATE.md), mit getrennten Schritten für fertige Images und lokale Quellcode-Builds.

**Datenbank, `.env` mit Fernet-Schlüssel, Compose-Konfiguration und beide Bildbestände (`image_data` und `ml_data` beziehungsweise `ml/`) zusammen sichern.** Backend und Worker müssen während der Sicherung gestoppt sein, damit Datenbank und Dateien zusammenpassen. Ohne Fernet-Schlüssel sind gespeicherte FTP-/SMTP-Zugangsdaten nicht mehr lesbar.

Migrationen werden beim Backend-Start automatisch angewendet. Konten, Zuordnungen und bestätigte Lernbilder bleiben erhalten. Die neue Aufbewahrung gilt auch für vorhandene unbewertete automatische Bilder. Für einen Rückwechsel eine vollständige Sicherung passend zur alten Software wiederherstellen; `docker compose down -v` ist kein Update-Schritt.

## Lokale Entwicklung

Python 3.12+, Node.js 22+ und npm. Die Lockdateien sind Bestandteil des Projekts.

```bash
cd backend
uv sync --frozen
# DATABASE_URL, ENCRYPTION_KEY, DATA_DIR, ML_DATA_DIR, COOKIE_SECURE=false exportieren
uv run alembic upgrade head
uv run python -m app.cli create-admin --email admin@firma.de
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

In einem zweiten Terminal mit denselben Backend-Variablen `uv run python -m app.worker` starten. Für einen schnellen lokalen Funktionstest ist `DATABASE_URL=sqlite:///./dev.db` möglich; Ziel-Datenbank und Compose verwenden PostgreSQL.

```bash
cd frontend
npm ci
BACKEND_INTERNAL_URL=http://127.0.0.1:8000 npm run dev
```

Bei einem Unterverzeichnis `APP_BASE_PATH` in beiden Prozessen übereinstimmend setzen. Für reproduzierbare Builds wird Webpack verwendet; der Turbopack-Hilfsport war in der Ausführungsumgebung blockiert. Schriften sind Systemschriften, ohne externe Font-Anfragen.

## Prüfung und Release

```bash
cd backend
uv run pytest -q
# Optional gegen eine ausschließlich für Tests angelegte PostgreSQL-Datenbank:
FILLENS_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/fillens_test uv run pytest -q
cd ../frontend
npm run typecheck
npm run build
cd ..
python3 scripts/package_release.py
```

Die Tests leeren ihre eigene Testdatenbank. Externe Test-URLs werden nur akzeptiert, wenn die Datenbank genau `fillens_test` heißt. Paket und SHA256-Datei entstehen in `../release`. Das Paket enthält Quellen und Lockdateien; Docker baut die Images auf dem Zielrechner.

Fertige Images entstehen über GitHub Actions: Frontend- und Backend-Version angleichen, `docs/CHANGELOG.md` ergänzen und einen Tag `vX.Y.Z` auf `main` pushen. Der Workflow `Release-Images` baut beide Images für `linux/amd64` und `linux/arm64` und veröffentlicht sie mit den Tags `X.Y.Z`, `X.Y` und `latest` in der GitHub Container Registry. Bei jedem Push und Pull Request laufen Backend-Tests (SQLite und PostgreSQL), TypeScript-Prüfung und Frontend-Build.

Spezifikation: [docs/SOFTWARE_SPEC.md](docs/SOFTWARE_SPEC.md). Änderungen: [docs/CHANGELOG.md](docs/CHANGELOG.md). Architektur: [ARCHITECTURE.md](ARCHITECTURE.md). KI-Roadmap: [ML.md](ML.md).

## Backup des Trainingsspeichers

Das Docker-Volume `fillens_ml_data` enthält Lernbilder und Modellartefakte; auf Synology liegt es als Ordner `ml/` vor. Zusätzlich `image_data`, Datenbank und Konfiguration konsistent sichern, wie in [docs/UPDATE.md](docs/UPDATE.md) beschrieben. Die Wiederherstellung auf der eigenen Zielinstallation vor produktiven Updates erproben.


## Sammlung und Nutzerprüfung

Bei „Weiter lernen“ sichert der Worker jedes neue importierte Bild sofort als Lernreferenz, auch ohne fertiges Modell, ROI oder eingeschaltete Erkennung. Vorhandene lokale Bilder werden beim Einschalten nachgeholt. Die ergänzende FTP-Nachsammlung unterstützt Datumsordner und sonstige Verzeichnisstrukturen, maximal zehn Downloads und 30 Sekunden je Kamera. Historische Trainingsabrufe erzeugen keine rückwirkenden Messungen. Die früheren Grenzen von drei Kamerabildern und 50 offenen Lernbildern sind aufgehoben.

Normale Nutzer sehen Training nur für Behälter mit eingeschaltetem Lernen. Sie dürfen vorhandene Bilder ansehen, Füllstände bestätigen/nachbearbeiten und offene Bilder verwerfen. Upload, Lernschalter, Bildbereich, Bildmodus/Aufnahmegruppe, Modellverwaltung und technische Einstellungen bleiben beim Admin. Ist alles pausiert, verschwindet der Reiter; direkte Trainings-/Referenzzugriffe werden ebenfalls abgewiesen.

Verwerfen ist dauerhaft und verhindert erneutes Sammeln desselben Bildes. Dateien werden nach Ende eventuell darauf angewiesener Trainings entfernt; Messwerte bleiben erhalten. Bestätigte Referenzen bleiben für spätere Trainings und Qualitätsprüfungen gespeichert. Für die laufende Erkennung braucht ein fertiges Modell die Originalbilder nicht mehr. Migration 0007 sperrt den Rückwechsel bei verworfenen Referenzen; dafür eine passende DB-/ML-Sicherung wiederherstellen.

Die gestrichelten Warnschwellen im Dashboard entfallen. Kachelfarben und Benachrichtigungen bleiben erhalten. Ratenkurven verbinden gültige Werte geradlinig und verlaufen bei null auf der Nulllinie; echte Berechnungslücken und unterschiedliche Zyklen bleiben getrennt.

## Mitwirken

Beiträge sind willkommen. Ablauf, Entwicklungsumgebung und Commit-Regeln (deutsche Commit-Messages) stehen in [CONTRIBUTING.md](CONTRIBUTING.md). Sicherheitslücken bitte nicht öffentlich melden, sondern wie in [SECURITY.md](SECURITY.md) beschrieben.

## Lizenz

Fillens steht unter der [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later). Wer eine veränderte Fassung als Netzwerkdienst betreibt, muss deren Quellcode den Nutzern zugänglich machen; das Portal verlinkt dafür im Fuß- und Anmeldebereich auf den Quellcode. Drittanbieterabhängigkeiten stehen unter ihren eigenen Lizenzen.

### Ungeeignete Bilder lernen

Im Training ein Bild auswählen, den Grund (z. B. **Deckel zu**) angeben und **Als ungeeignet lernen** drücken. Es bleibt als bestätigtes Gegenbeispiel ohne Füllstand erhalten. **Nur verwerfen** entfernt ein Bild ohne Training. Unter „Bestätigt“ lassen sich Bewertungen korrigieren. Bereits gelöschte Bilder können nicht nachträglich trainiert werden.

Die Erkennung benötigt messbare und ungeeignete Beispiele aus mehreren unabhängigen Aufnahmegruppen. Der Lernstatus zeigt, für welche Bildmodi automatisches Verwerfen freigegeben ist. Erkannte ungeeignete Bilder erzeugen keinen Messwert und können bei eingeschaltetem Lernen im begrenzten Prüffenster kontrolliert werden. Bereits gespeicherte Messwerte bleiben bei einer späteren Neubewertung erhalten.

### Laufend sammeln, Training steuern

Unter **Training** stellen Admins je Kamera die Aufbewahrung automatischer unbewerteter Bilder (1–365 Tage, Standard sieben) und **Automatisch trainieren / Training manuell starten** ein. Manuelle Uploads und bestätigte positive/negative Lernbilder bleiben erhalten. **Training starten** verwendet den aktuellen bestätigten Bestand und dieselben Qualitätsprüfungen wie die Automatik. Ein bereits laufendes Training darf bei einem Moduswechsel fertig werden.

Die aktuelle Kameravorschau bleibt sichtbar. Neue Bilder werden während einer Eingabe angekündigt; **Neueste Bilder anzeigen** wechselt ausdrücklich zur neuen Seite. Aufnahme- und Importzeit werden getrennt angezeigt. Bei Verkürzung der Aufbewahrung wird vor dem Speichern angezeigt, wie viele unbewertete Lernbilder betroffen sind. Beim Update DB sowie `image_data` und `ml_data` sichern: Die neue Bereinigung gilt auch für bestehende unbewertete automatische Bilder.
