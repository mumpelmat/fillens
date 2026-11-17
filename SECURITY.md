# Sicherheit

## Sicherheitslücken melden

Bitte Sicherheitslücken **nicht** als öffentliches Issue melden. Stattdessen auf GitHub unter **Security → Report a vulnerability** einen privaten Bericht anlegen (GitHub Security Advisories).

Hilfreich sind: betroffene Version, Schritte zur Reproduktion, erwartete und tatsächliche Auswirkung. Wir bestätigen den Eingang und stimmen Behebung und Veröffentlichung mit dir ab.

*English: Please report vulnerabilities privately via GitHub's "Report a vulnerability" button, not as a public issue.*

## Unterstützte Versionen

Sicherheitskorrekturen erfolgen für die jeweils neueste veröffentlichte Version.

## Hinweise für Betreiber

- Portal nur über HTTPS veröffentlichen (`COOKIE_SECURE=true`); API und Datenbank sind in Compose bewusst nicht von außen erreichbar.
- `ENCRYPTION_KEY` und Datenbank-Dumps wie Zugangsdaten behandeln.
- Images regelmäßig auf die neueste Version aktualisieren.

## Updater-Dienst (Update per Klick, ab 0.8.0)

Der Dienst `updater` in `deploy/synology/compose.yaml` bindet `/var/run/docker.sock` und den Projektordner ein. **Zugriff auf den Docker-Socket entspricht Root-Rechten auf dem Host.** Schutzmaßnahmen:

- Kein veröffentlichter Port; erreichbar nur im internen Compose-Netz, ausschließlich für das Backend vorgesehen.
- Jede Anfrage braucht `UPDATER_TOKEN` (mindestens 32 Zeichen, Vergleich in konstanter Zeit). Ohne Token startet der Dienst nicht.
- Auslösung nur durch angemeldete Admins mit CSRF-Schutz. Die Zielversion muss dem zuvor von GitHub geprüften Release entsprechen und ist auf das Format `X.Y.Z` beschränkt; Image-Namen und Release-Quelle stammen aus der Konfiguration, nie aus der Anfrage.
- Der Updater ändert in der `.env` nur die Zeile `FILLENS_VERSION` und verweigert Updates, die die `compose.yaml` ändern würden.
- Ausgehende Verbindungen: Das Backend fragt GitHub-Releases nur auf Klick ab; der Updater lädt die Release-Beschreibung und Images von GitHub bzw. GHCR.

Wer das nicht möchte, entfernt den Dienst `updater` aus der Compose-Datei; `UPDATER_TOKEN` ist dann nicht nötig. Das Portal zeigt dann nur die Update-Anleitung; Updates erfolgen manuell nach [docs/UPDATE.md](docs/UPDATE.md).
