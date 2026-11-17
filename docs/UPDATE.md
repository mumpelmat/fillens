# Bestehende Installationen aktualisieren (aktuell: 0.8.1)

Für Docker-Compose-Installationen ab 0.5.2. Es ist keine Neuinstallation erforderlich. Vorhandene Benutzer, Passwörter, Rollen, FTP-Zuordnungen, Messwerte, bestätigte Lernbilder und Modelle bleiben erhalten. Ältere Ausgangsversionen zuerst in einer Kopie prüfen.

| Ausgangsversion | Datenbank |
|---|---|
| 0.8.0, 0.7.1, 0.7.0, 0.6.0 | Keine Migration; Schema bleibt `0011`. Nur Images bzw. Quellen wechseln. |
| 0.5.2 | Migrationen 0009–0011 laufen beim Backend-Start automatisch. |

Neu in 0.8.1: Im Dashboard ersetzt ein neues Kamerabild das vorherige, statt sich darunter anzuhängen. Neu in 0.8.0: **Update per Klick im Portal** für Installationen mit fertigen Images (Synology/Server). `/api/health` meldet zusätzlich die installierte Version. Neu in 0.7.1: Login-Seite und Fußzeile zeigen die installierte Version. Neu in 0.7.0: Kontoverwaltung unter **Benutzer** und **Füllstand korrigieren** unter dem Kamerabild.

Beim Wechsel von 0.5.2: Automatisch gesammelte, unbewertete Bilder erhalten standardmäßig **sieben Tage Aufbewahrung**, einstellbar je Kamera von 1 bis 365 Tagen. Bestätigte Beispiele, manuelle Uploads und die neueste Kameravorschau bleiben geschützt. Deshalb vor dem ersten Start der neuen Version auch beide Bildbestände sichern.

## Welcher Weg?

| Installation | Ab 0.8.0 | Umstieg auf 0.8.0 |
|---|---|---|
| Fertige Images (Synology, `deploy/synology/compose.yaml`) | **System → Software-Update** im Portal ([Abschnitt A](#a-update-im-portal)) | Einmalig manuell ([Abschnitt B](#b-einmaliger-umstieg-auf-080-mit-update-funktion)) |
| Quellcode-Build (Root-`compose.yaml`) | Manuell ([Abschnitt C](#c-manuelles-update)) | Manuell |

## A. Update im Portal

Voraussetzung: Installation mit fertigen Images, Dienst `updater` und `UPDATER_TOKEN` in der `.env` (ab 0.8.0, siehe B).

1. Kamerabilder und Lernbestand sichern (Docker-Volume `image_data` und Ordner `ml/`, siehe [C.1](#1-konsistent-sichern)). Die Datenbank sichert der Updater selbst.
2. Als Administrator **System → Software-Update → Nach Updates suchen**. Das Portal fragt nur bei diesem Klick die GitHub-Releases ab und zeigt Version und Änderungen.
3. **Update auf X installieren** bestätigen. Der Updater
   1. lädt die neuen Images (bei Fehler: Abbruch, nichts verändert),
   2. legt `backups/vor-X-<Zeitpunkt>/` mit `.env`, `compose.yaml` und einem geprüften Datenbank-Dump an (bei Fehler: Abbruch, nichts verändert),
   3. stoppt Worker und Backend, setzt in der `.env` ausschließlich `FILLENS_VERSION=X` und startet Backend, Worker und Portal neu; Migrationen laufen dabei automatisch,
   4. wartet, bis die neue Version gesund läuft, und erneuert zuletzt sich selbst.
4. Die Seite lädt danach automatisch neu; Fußzeile und Login-Seite zeigen die neue Version.

Das Portal ist während des Neustarts einige Minuten nicht erreichbar. Fortschritt und Ergebnis stehen auch in `backups/update-status.json`.

Update per Klick wird abgelehnt, wenn das Release die `compose.yaml` ändert oder die lokale `compose.yaml` angepasst wurde. Dann gilt Abschnitt C. Schlägt das Update nach dem Versionswechsel fehl, gibt es kein automatisches Zurück, weil Migrationen bereits gelaufen sein können: Protokolle prüfen (`docker compose logs --tail=100 backend worker frontend updater`) und gegebenenfalls den [Rückweg](#rückweg) mit der angelegten Sicherung gehen.

## B. Einmaliger Umstieg auf 0.8.0 mit Update-Funktion

Gilt für bestehende Image-Installationen (0.5.2–0.7.1). Der Umstieg erfolgt direkt auf die aktuelle Version 0.8.1. Zuerst wie in [C.1](#1-konsistent-sichern) sichern, dann im bisherigen Projektordner:

1. Die neue [`deploy/synology/compose.yaml`](../deploy/synology/compose.yaml) über die bisherige `compose.yaml` kopieren. Eigene Anpassungen der alten Datei nicht übernehmen, sondern als `compose.override.yaml` daneben legen; sonst ist Update per Klick später gesperrt.
2. In der **vorhandenen** `.env` zwei Zeilen ergänzen bzw. ändern, alle übrigen Werte unverändert lassen:

   ```dotenv
   FILLENS_VERSION=0.8.1
   UPDATER_TOKEN=<Ausgabe von: openssl rand -hex 32>
   ```

3. `docker compose up -d --pull always` (Synology: per SSH mit `sudo`, oder im Container Manager das Projekt stoppen und neu erstellen, siehe [SYNOLOGY.md](SYNOLOGY.md#7-update)).
4. Prüfen wie in [C.3](#3-prüfen); unter **System → Software-Update** muss „Installiert: Version 0.8.1“ ohne Warnung stehen.

Der Dienst `updater` erhält Zugriff auf den Docker-Socket und damit Root-Rechte auf dem Host; Hinweise und Abschalten in [SECURITY.md](../SECURITY.md).

## C. Manuelles Update

Die folgenden Befehle im **bisherigen Compose-Projektordner** ausführen, auf Synology gegebenenfalls mit `sudo`. Bestehenden Projektnamen, Volume-Namen, Mounts und zusätzliche Compose-Dateien beibehalten. `.env` nicht durch die Vorlage ersetzen; insbesondere `ENCRYPTION_KEY` und `POSTGRES_PASSWORD` unverändert lassen. Kein `init_env.py` und kein `create-admin` für ein Update ausführen.

### 0. Aktuellen Stand ermitteln

```bash
grep FILLENS_VERSION .env || echo "FILLENS_VERSION fehlt: es läuft das Image-Tag latest"
docker compose images
curl -s http://localhost:3000/api/health
```

### 1. Konsistent sichern

Wartungsfenster einplanen, laufende Trainings möglichst abschließen lassen und Uploads beenden. Während der Sicherung und Migration ist das Portal nicht nutzbar. Genügend freien Speicher für Datenbank und Bilddateien vorhalten.

```bash
set -eu
umask 077
backup_dir="backups/vor-update-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
cp .env compose.yaml "$backup_dir/"
docker compose images > "$backup_dir/images.txt"
docker compose stop worker backend
docker compose exec -T postgres pg_dump -U fillens -d fillens -Fc > "$backup_dir/fillens.dump"
docker compose exec -T postgres pg_restore --list < "$backup_dir/fillens.dump" > "$backup_dir/database-contents.txt"
docker compose run --rm --no-deps -T --user root --entrypoint tar worker -C / -cf - data ml > "$backup_dir/images.tar"
tar -tf "$backup_dir/images.tar" > "$backup_dir/image-contents.txt"
```

Der Hilfscontainer liest die vorhandenen Worker-Mounts; er startet weder Import noch Training. Das Archiv enthält damit sowohl `image_data` als auch `ml_data` beziehungsweise den Synology-Ordner `ml/`. Eigene Compose-Overrides ebenfalls sichern. Sicherung verschlüsselt auf einen anderen Datenträger kopieren und ihre Wiederherstellbarkeit prüfen. Fehler beim Sichern zuerst beheben; nicht mit einem unvollständigen Backup fortfahren.

### 2a. Fertige Images / Synology

In der **vorhandenen** `.env` die Zeile `FILLENS_VERSION` auf die Zielversion setzen. **Fehlt die Zeile, läuft das Tag `latest`** – die Zeile dann ergänzen, damit das Update eindeutig ist:

```dotenv
FILLENS_VERSION=0.8.1
```

Dann die Images ausdrücklich neu laden und starten:

```bash
docker compose pull
docker compose up -d --pull always
```

Ohne Pull verwendet Docker ein bereits vorhandenes Image weiter. Ein bloßer Neustart (auch „Neu starten“ im Container Manager) aktualisiert nichts. Frontend und Backend werden für `linux/amd64` und `linux/arm64` bereitgestellt; Worker und Backend nutzen dasselbe Image. Ein eigener `APP_BASE_PATH` erfordert weiterhin einen Quellcode-Build.

### 2b. Installation aus dem Quellcode

Eigene Quelländerungen vorab separat sichern und mit dem Release abgleichen. In einem unveränderten Git-Checkout:

```bash
git fetch origin --tags
git checkout v0.8.1
docker compose build backend worker frontend
docker compose up -d
```

Alternativ das Quell-ZIP samt SHA256-Datei aus dem [Release](https://github.com/mumpelmat/fillens/releases/tag/v0.8.1) herunterladen und vor dem Entpacken `shasum -a 256 -c fillens-0.8.1-source.zip.sha256` ausführen. Quellen ersetzen, eigene `.env`, Compose-Anpassungen und Datenverzeichnisse erhalten. Die Dienste weiterhin mit demselben Compose-Projektnamen und denselben Volumes starten.

### 3. Prüfen

```bash
docker compose ps
docker compose exec -T backend alembic current
curl --fail http://localhost:3000/api/health
```

Erwartet: Schema `0011 (head)`, gesunde Dienste und `{"status":"ok","version":"0.8.1"}`. Bei abweichendem Port oder Unterverzeichnis die URL anpassen. Bei Fehlern `docker compose logs --tail=100 backend worker frontend` prüfen und den Worker bis zur Klärung gestoppt lassen.

Mit einem bestehenden Konto anmelden. Fußzeile und Login-Seite zeigen die Version; zeigt der Browser noch die alte Oberfläche, die Seite hart neu laden (Strg+Umschalt+R bzw. Cmd+Umschalt+R). Behälter, Kameras, Messungen und Lernbewertungen kontrollieren.

## Fehlersuche

| Symptom | Ursache und Abhilfe |
|---|---|
| Nach dem Update läuft weiter die alte Version | `FILLENS_VERSION` in der `.env` steht noch auf der alten Version **oder** fehlt (dann `latest` aus dem lokalen Zwischenspeicher). Zeile setzen und `docker compose up -d --pull always`. Ein Neustart allein lädt keine neuen Images. Prüfen mit `curl …/api/health`. |
| Fußzeile zeigt alte Version, `/api/health` die neue | Browser-Zwischenspeicher: Seite hart neu laden. |
| Software-Update: „Kein Updater eingerichtet“ | Quellcode-Installation oder Umstieg B noch nicht erfolgt. |
| Software-Update: „Updater nicht erreichbar“ / „lehnt den Zugriff ab“ | `docker compose ps updater` und `docker compose logs updater` prüfen; `UPDATER_TOKEN` in der `.env` muss gesetzt und mindestens 32 Zeichen lang sein. |
| Software-Update: „ändert die compose.yaml“ | Release ändert die Compose-Datei oder sie wurde lokal angepasst: manuell nach B bzw. C aktualisieren. |
| Software-Update: „keine Update-Beschreibung“ | Release ohne `fillens-X-update.json` (z. B. vor 0.8.0): manuell aktualisieren. |

## Rückweg

Ein älteres Image allein stellt den alten Datenstand nicht wieder her. Migrationen schützen vor verlustbehaftetem Downgrade; ältere Versionen kennen neuere Lernzustände und Aufbewahrungsregeln nicht.

Frontend, Backend und Worker stoppen. In einer getrennten Wiederherstellungsumgebung die **passende alte Version**, gesicherte Konfiguration und denselben Fernet-Schlüssel verwenden. Den Datenbank-Dump mit `pg_restore` in eine leere PostgreSQL-Datenbank und `images.tar` in leere `/data`- und `/ml`-Mounts des Workers zurückspielen; Dateieigentümer und Rechte erhalten. Erst nach erfolgreicher Prüfung umschalten. Die Wiederherstellung auf der jeweiligen Zielinstallation erproben; eine bloße Dateikopie des laufenden PostgreSQL-Datenordners reicht nicht.

`docker compose down -v` löscht Volumes und gehört weder zum Update noch zur Sicherung.
