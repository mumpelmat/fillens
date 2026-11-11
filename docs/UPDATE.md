# Bestehende Installationen auf 0.7.0 aktualisieren

Für Docker-Compose-Installationen ab 0.5.2. Es ist keine Neuinstallation erforderlich. Vorhandene Benutzer, Passwörter, Rollen, FTP-Zuordnungen, Messwerte, bestätigte Lernbilder und Modelle bleiben erhalten. Ältere Ausgangsversionen zuerst in einer Kopie prüfen.

| Ausgangsversion | Datenbank |
|---|---|
| 0.6.0 | Keine Migration; Schema bleibt `0011`. Nur Images bzw. Quellen wechseln. |
| 0.5.2 | Migrationen 0009–0011 laufen beim Backend-Start automatisch. |

Neu in 0.7.0: Admins ändern Rollen, setzen Passwörter und löschen Konten unter **Benutzer**. Im Dashboard ersetzt **Füllstand korrigieren** unter dem Kamerabild die bisherigen Aktionen „Jetzt bewerten“ und „Messwert eintragen“. Die Korrektur ändert den Messwert des neuesten Bildes und speichert ihn als bestätigtes Lernbild.

Beim Wechsel von 0.5.2: Automatisch gesammelte, unbewertete Bilder erhalten standardmäßig **sieben Tage Aufbewahrung**, einstellbar je Kamera von 1 bis 365 Tagen. Das gilt auch für vorhandene Bilder. Bestätigte positive/negative Beispiele, manuelle Uploads und die neueste Kameravorschau bleiben geschützt. Deshalb vor dem ersten Start der neuen Version auch beide Bildbestände sichern.

Die folgenden Befehle im **bisherigen Compose-Projektordner** ausführen, auf Synology gegebenenfalls mit `sudo`. Bestehenden Projektnamen, Volume-Namen, Mounts und zusätzliche Compose-Dateien beibehalten. `.env` nicht durch die Vorlage ersetzen; insbesondere `ENCRYPTION_KEY` und `POSTGRES_PASSWORD` unverändert lassen. Kein `init_env.py` und kein `create-admin` für ein Update ausführen.

## 1. Konsistent sichern

Wartungsfenster einplanen, laufende Trainings möglichst abschließen lassen und Uploads beenden. Während der Sicherung und Migration ist das Portal nicht nutzbar. Genügend freien Speicher für Datenbank und Bilddateien vorhalten.

```bash
set -eu
umask 077
backup_dir="backups/vor-0.7.0-$(date +%Y%m%d-%H%M%S)"
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

## 2a. Fertige Images / Synology

In der **vorhandenen** `.env` ausschließlich die Versionsauswahl ändern:

```dotenv
FILLENS_VERSION=0.7.0
```

Dann:

```bash
docker compose pull backend worker frontend
docker compose up -d
```

Frontend und Backend werden für `linux/amd64` und `linux/arm64` bereitgestellt; der Worker verwendet dasselbe Backend-Image. Ein eigener `APP_BASE_PATH` erfordert weiterhin einen Quellcode-Build. Datenbank- und Hilfsimages werden bei diesem Schritt nicht absichtlich aktualisiert.

## 2b. Installation aus dem Quellcode

Eigene Quelländerungen vorab separat sichern und mit dem Release abgleichen. In einem unveränderten Git-Checkout:

```bash
git fetch origin --tags
git checkout v0.7.0
docker compose build backend worker frontend
docker compose up -d
```

Alternativ das Quell-ZIP samt SHA256-Datei aus dem [Release](https://github.com/mumpelmat/fillens/releases/tag/v0.7.0) herunterladen und vor dem Entpacken `shasum -a 256 -c fillens-0.7.0-source.zip.sha256` ausführen. Quellen ersetzen, eigene `.env`, Compose-Anpassungen und Datenverzeichnisse erhalten. Die Dienste weiterhin mit demselben Compose-Projektnamen und denselben Volumes starten.

## 3. Prüfen

```bash
docker compose ps
docker compose exec -T backend alembic current
curl --fail http://localhost:3000/api/health
```

Erwartet: Schema `0011 (head)`, gesunde Dienste und erfolgreiche HTTP-Antwort. Bei abweichendem Port oder Unterverzeichnis die URL anpassen. Bei Fehlern `docker compose logs --tail=100 backend worker frontend` prüfen und den Worker bis zur Klärung gestoppt lassen.

Mit einem bestehenden Konto anmelden. Behälter, Kameras, Messungen und Lernbewertungen kontrollieren. Bei aktivem Lernen sollen neue Kamerabilder unabhängig von Modell und ROI in der Lernansicht erscheinen. Unter **Benutzer** stehen Rollenwahl, **Passwort setzen** und **Löschen** bereit; im Dashboard erscheint **Füllstand korrigieren** unter dem Kamerabild. Administratoren können Aufbewahrung und automatischen/manuellen Trainingsstart je Kamera einstellen. Die Erkennung bleibt separat steuerbar. Passwortwiederherstellung per E-Mail setzt eine funktionierende SMTP-Konfiguration voraus.

## Rückweg

Ein älteres Image allein stellt den alten Datenstand nicht wieder her. Migrationen schützen vor verlustbehaftetem Downgrade; ältere Versionen kennen die neuen Lernzustände und Aufbewahrungsregeln nicht.

Frontend, Backend und Worker stoppen. In einer getrennten Wiederherstellungsumgebung die **passende alte Version**, gesicherte Konfiguration und denselben Fernet-Schlüssel verwenden. Den Datenbank-Dump mit `pg_restore` in eine leere PostgreSQL-Datenbank und `images.tar` in leere `/data`- und `/ml`-Mounts des Workers zurückspielen; Dateieigentümer und Rechte erhalten. Erst nach erfolgreicher Prüfung umschalten. Die Wiederherstellung auf der jeweiligen Zielinstallation erproben; eine bloße Dateikopie des laufenden PostgreSQL-Datenordners reicht nicht.

`docker compose down -v` löscht Volumes und gehört weder zum Update noch zur Sicherung.
