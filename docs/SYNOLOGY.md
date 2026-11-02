# Fillens auf einer Synology installieren

Diese Anleitung beschreibt den Betrieb mit dem **Container Manager** (DSM 7.2 oder neuer) und den fertigen Images aus der GitHub Container Registry. Auf dem NAS wird nichts gebaut. Images gibt es für `amd64` (Intel/AMD-Modelle, z. B. DS224+, DS923+) und `arm64` (z. B. DS223, DS124).

| Dienst | Image | Daten |
|---|---|---|
| `postgres` | `postgres:17-alpine` | Ordner `postgres/` |
| `backend` (API) | `ghcr.io/mumpelmat/fillens-backend` | – |
| `worker` | `ghcr.io/mumpelmat/fillens-backend` | Bildpuffer (Docker-Volume), Ordner `ml/` |
| `frontend` (Portal) | `ghcr.io/mumpelmat/fillens-frontend` | – |
| `ml-init` | `busybox` | setzt einmalig die Rechte für `ml/` und beendet sich |

## 1. Projektordner anlegen

In der **File Station** im freigegebenen Ordner `docker` einen Ordner `fillens` anlegen, also `/volume1/docker/fillens`. Darin:

- [`deploy/synology/compose.yaml`](../deploy/synology/compose.yaml) als `compose.yaml`
- [`deploy/synology/.env.example`](../deploy/synology/.env.example) als `.env`

Die Ordner `postgres/` und `ml/` legt Docker beim ersten Start selbst an.

## 2. Secrets in `.env` eintragen

`POSTGRES_PASSWORD` und `ENCRYPTION_KEY` müssen zufällig erzeugt werden. Der Fernet-Schlüssel verschlüsselt die gespeicherten FTP-/SMTP-Passwörter und muss dauerhaft sicher aufbewahrt werden: Ohne ihn sind diese Zugangsdaten nach einer Wiederherstellung nicht mehr lesbar.

Per SSH auf der Synology (oder auf einem beliebigen Rechner mit OpenSSL):

```bash
openssl rand -hex 32                          # → POSTGRES_PASSWORD
openssl rand -base64 32 | tr '+/' '-_'        # → ENCRYPTION_KEY
```

Alternativ auf einem Rechner mit Python im Repository `python3 scripts/init_env.py` ausführen und die beiden Werte aus der erzeugten `.env` übernehmen.

Weitere Werte:

| Variable | Empfehlung |
|---|---|
| `FILLENS_VERSION` | Feste Version wie `0.5.2` statt `latest`, damit Updates bewusst erfolgen |
| `COOKIE_SECURE` | `true` bei Zugriff über HTTPS (Reverse Proxy); nur für einen reinen HTTP-Test im LAN `false` |
| `BIND_ADDRESS` | `127.0.0.1`, wenn der DSM-Reverse-Proxy genutzt wird; `0.0.0.0` für direkten LAN-Zugriff über `http://<NAS-IP>:3000` |
| `PORT` | Freier Port auf dem NAS, Standard `3000` |

Alle übrigen Variablen sind im [README](../README.md#konfiguration) beschrieben.

## 3. Projekt im Container Manager anlegen

1. **Container Manager → Projekt → Erstellen**.
2. Projektname `fillens`, Pfad `/volume1/docker/fillens`, Quelle **Vorhandene compose.yaml verwenden**.
3. Web-Portal-Einstellungen überspringen und **Fertig** wählen. Der Container Manager lädt die Images und startet die Dienste. `ml-init` beendet sich danach planmäßig; die übrigen Dienste zeigen nach etwa einer Minute **Fehlerfrei**.

Alternativ per SSH im Projektordner: `sudo docker compose up -d`.

Hinweis: Dass der Container Manager die `.env` im Projektordner bei jeder DSM-Version für die Variablenersetzung auswertet, ist noch auf realer Hardware zu bestätigen. Meldet das Projekt `POSTGRES_PASSWORD in .env setzen`, das Projekt per SSH mit `sudo docker compose up -d` im Projektordner starten.

## 4. Ersten Administrator anlegen

Per SSH:

```bash
cd /volume1/docker/fillens
sudo docker compose exec backend python -m app.cli create-admin --email admin@firma.de
```

Oder im Container Manager unter **Container → fillens-backend-1 → Terminal** mit dem Befehl `python -m app.cli create-admin --email admin@firma.de`. Das Passwort wird verdeckt abgefragt (mindestens zwölf Zeichen). Es gibt keine voreingestellten Benutzer.

## 5. HTTPS über den DSM-Reverse-Proxy

1. **Systemsteuerung → Anmeldeportal → Erweitert → Reverse Proxy → Erstellen**.
2. Quelle: Protokoll `HTTPS`, Hostname z. B. `fuellstand.example.de`, Port `443`.
3. Ziel: Protokoll `HTTP`, Hostname `localhost`, Port `3000` (bzw. `PORT`).
4. Unter **Systemsteuerung → Sicherheit → Zertifikat** ein Let's-Encrypt-Zertifikat für den Hostnamen anlegen und dem Reverse-Proxy-Eintrag zuweisen.

Die fertigen Images laufen an der Domainwurzel bzw. unter einer Subdomain. Ein Unterverzeichnis (`APP_BASE_PATH`, z. B. `/fuellstand`) wird beim Frontend-Build festgelegt und erfordert ein selbst gebautes Image (siehe README, Abschnitt „Domain, Unterverzeichnis und Reverse Proxy“).

## 6. Backup

Zu sichern sind **Datenbank, `.env` (Fernet-Schlüssel) und der Ordner `ml/`**. Den Bildpuffer (Docker-Volume `fillens_image_data`) nicht sichern; er baut sich aus neuen FTP-Importen auf. Den Ordner `postgres/` nicht im laufenden Betrieb als Dateikopie sichern, sondern einen Dump erzeugen.

Täglicher Dump über **Systemsteuerung → Aufgabenplaner → Erstellen → Geplante Aufgabe → Benutzerdefiniertes Script**, Benutzer `root`:

```bash
cd /volume1/docker/fillens && mkdir -p backups && chmod 700 backups && \
docker compose exec -T postgres pg_dump -U fillens -d fillens -Fc > backups/fillens-$(date +%F).dump && \
chmod 600 backups/fillens-$(date +%F).dump && \
find backups -name 'fillens-*.dump' -mtime +14 -delete
```

Anschließend `/volume1/docker/fillens` mit **Hyper Backup** sichern und dabei `postgres/` ausschließen. Konsistent sind Dump und `ml/` desselben Zeitpunkts, aufgenommen ohne laufende Trainings oder Uploads. Wiederherstellung wie im README unter „Backup und Update“.

## 7. Update

1. Backup erstellen (Schritt 6).
2. In `.env` `FILLENS_VERSION` auf die neue Version setzen (siehe [Änderungsprotokoll](CHANGELOG.md) und GitHub-Releases).
3. Container Manager → Projekt `fillens` → **Aktion → Erstellen** (lädt die neuen Images und startet neu) bzw. per SSH `sudo docker compose pull && sudo docker compose up -d`.

Datenbankmigrationen laufen beim Start des Backends automatisch. Nicht auf ältere Versionen zurückwechseln, ohne die passende Sicherung wiederherzustellen. `docker compose down -v` löscht den Bildpuffer und ist kein Update-Schritt.

## Fehlersuche

| Symptom | Prüfung |
|---|---|
| Image lässt sich nicht laden (`denied`/`unauthorized`) | Pakete in der GitHub Container Registry sind noch nicht öffentlich oder die Version existiert nicht |
| Backend startet nicht, `ml-init` fehlgeschlagen | Rechte des Ordners `/volume1/docker/fillens`; `ml-init`-Protokoll im Container Manager ansehen |
| Anmeldung schlägt ohne Fehlermeldung fehl | `COOKIE_SECURE=true` bei Zugriff über HTTP; HTTPS nutzen oder für Tests `false` setzen |
| Portal nicht erreichbar | `BIND_ADDRESS`/`PORT` und Reverse-Proxy-Ziel prüfen; Status der Dienste im Container Manager |
| Keine Kamerabilder | Unter **System → Kameras & Bildpuffer** Fehlercode ablesen (Tabelle im README) |
