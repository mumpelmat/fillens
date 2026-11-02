# Mitwirken an Fillens

Danke für dein Interesse! Fehlerberichte, Ideen und Pull Requests sind willkommen.

*English: Issues and pull requests in English are welcome too. The rules below (German commit messages, German docs with umlauts) apply to the code base; maintainers can help translate if needed.*

## Ablauf

1. Für größere Änderungen zuerst ein Issue eröffnen und den Ansatz abstimmen.
2. Repository forken und einen Branch anlegen, z. B. `feature/csv-export` oder `fix/ftp-timeout`.
3. Änderungen klein und nachvollziehbar halten; bestehende Architektur nicht ohne Abstimmung umbauen.
4. Tests und Typprüfung lokal ausführen (siehe unten).
5. Pull Request gegen `main` öffnen und die Vorlage ausfüllen. Die CI muss grün sein.

## Entwicklungsumgebung

Voraussetzungen: Python 3.12 mit [uv](https://docs.astral.sh/uv/), Node.js 22, Docker mit Compose v2.

- Komplettes System per Docker: README, Abschnitt „Schnellstart mit Docker“
- Backend/Frontend einzeln mit Hot Reload: README, Abschnitt „Lokale Entwicklung“

## Prüfung

```bash
cd backend && uv run pytest -q
cd frontend && npm run typecheck && npm run build
```

Optional zusätzlich gegen PostgreSQL (die Datenbank muss `fillens_test` heißen und wird geleert):

```bash
FILLENS_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/fillens_test uv run pytest -q
```

Neue Funktionen und Fehlerkorrekturen im Backend brauchen passende Tests in `backend/tests/`. Schemaänderungen nur über eine neue Alembic-Migration in `backend/migrations/versions/`, inklusive geprüftem Downgrade.

## Regeln

| Bereich | Regel |
|---|---|
| Commit-Messages | Deutsch, Präsens, kurz und technisch. Erste Zeile mit Typ: `Feature: …`, `Fix: …`, `Refactor: …`, `Dokumentation: …`. Keine generischen Nachrichten wie „update“. |
| Dokumentation | Deutsch mit Umlauten (ä, ö, ü statt ae, oe, ue). Keine Spekulation, Offenes als „Noch zu klären“ kennzeichnen. |
| Doku-Pflicht | Bei neuen Features, Änderungen an Geschäftslogik, DB-Schema, Abhängigkeiten, Authentifizierung/Rollen oder APIs `docs/SOFTWARE_SPEC.md` und `docs/CHANGELOG.md` aktualisieren. |
| Abhängigkeiten | Keine neuen Libraries ohne klaren Bedarf; neue Abhängigkeiten im PR begründen. Lockdateien (`uv.lock`, `package-lock.json`) mit committen. |
| Sicherheit | Keine Zugangsdaten, `.env`-Dateien, echte Kamerabilder oder personenbezogenen Daten committen. |

Beispiele für Commit-Messages:

- `Feature: CSV-Export für Messwerte hinzugefügt`
- `Fix: Zeitzonenfehler beim EXIF-Import behoben`
- `Dokumentation: Synology-Anleitung um Backup ergänzt`

## Releases

Releases erstellen die Maintainer: Versionen in `backend/pyproject.toml` und `frontend/package.json` angleichen, `docs/CHANGELOG.md` ergänzen, Tag `vX.Y.Z` pushen. Der Workflow `Release-Images` veröffentlicht daraufhin die Images in der GitHub Container Registry.

## Lizenz

Fillens steht unter der AGPL-3.0-or-later. Mit einem Pull Request erklärst du dich einverstanden, dass dein Beitrag unter dieser Lizenz veröffentlicht wird.
