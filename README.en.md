<p align="center">
  <img src="docs/images/logo.svg" alt="fillens" width="240">
</p>

<p align="center"><strong>Self-hosted fill level portal</strong> for bins, tanks and containers – camera images, local learning and warnings.</p>

<p align="center">
  <a href="https://github.com/mumpelmat/fillens/actions/workflows/ci.yml"><img src="https://github.com/mumpelmat/fillens/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/mumpelmat/fillens/tags"><img src="https://img.shields.io/github/v/tag/mumpelmat/fillens?label=version&color=1e634d" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-1e634d" alt="License AGPL-3.0"></a>
  <img src="https://img.shields.io/badge/Docker-amd64%20%7C%20arm64-1e634d?logo=docker&logoColor=white" alt="Docker amd64 | arm64">
</p>

<p align="center"><a href="README.md">Deutsch</a> · <a href="docs/SYNOLOGY.md">Synology guide (German)</a> · <a href="CONTRIBUTING.md">Contributing</a></p>

![Dashboard with fill level, current camera image and history](docs/images/uebersicht.png)

Fillens is a self-hosted portal for monitoring fill levels of bins, tanks and containers using camera images. Cameras upload pictures via FTP/FTPS; Fillens imports them, records fill levels manually or through a locally trained image model, computes fill/consumption rates and sends warnings in the portal and by e-mail. Everything runs on your own hardware, no cloud service involved.

> The user interface and the detailed documentation are in **German**. This page is a short English overview. Contributions in English are welcome.

## Features

- FTP/FTPS import with date-folder detection, deduplication and configurable retention per camera (seven days by default, no image-count limit)
- Containers with capacity in litres or m³, one camera per container
- Manual readings plus a local learning model (polygon ROI, day/night, automatic validation before a new model is used)
- Fill or consumption mode with smoothed rates, events (emptying/refill) and threshold warnings via portal and SMTP
- Fill-level correction right below the camera image; a correction becomes both the reading and a confirmed training example
- Roles (admin/user) with account management (change role, set password, delete), Argon2 passwords, CSRF protection, encrypted FTP/SMTP credentials

<table>
  <tr>
    <td width="50%"><img src="docs/images/training.png" alt="Confirm images and train"></td>
    <td width="50%"><img src="docs/images/anlagen.png" alt="Containers and warning rules"></td>
  </tr>
  <tr>
    <td align="center"><sub>Confirm images &amp; train the local model</sub></td>
    <td align="center"><sub>Containers, capacities and warning rules</sub></td>
  </tr>
</table>

Stack: Next.js/React/TypeScript frontend, FastAPI + SQLAlchemy backend, Python worker, PostgreSQL 17, Docker Compose.

## Install on a Synology NAS

Prebuilt images for `amd64` and `arm64` are published to the GitHub Container Registry:

- `ghcr.io/mumpelmat/fillens-backend`
- `ghcr.io/mumpelmat/fillens-frontend`

1. Create `/volume1/docker/fillens` and copy [deploy/synology/compose.yaml](deploy/synology/compose.yaml) and [deploy/synology/.env.example](deploy/synology/.env.example) (as `.env`) into it.
2. Generate secrets: `openssl rand -hex 32` → `POSTGRES_PASSWORD`, `openssl rand -base64 32 | tr '+/' '-_'` → `ENCRYPTION_KEY`. Keep the encryption key safe.
3. Container Manager → Project → Create → use existing `compose.yaml`.
4. Create the first admin: `sudo docker compose exec backend python -m app.cli create-admin --email admin@example.com`.
5. Put the DSM reverse proxy (HTTPS) in front of `localhost:3000`.

Full step-by-step guide incl. backup and updates (German): [docs/SYNOLOGY.md](docs/SYNOLOGY.md).

## Other Docker hosts and development

On any Docker host you can build from source with `python3 scripts/init_env.py --http && docker compose up -d --build`. Local development setup, tests and configuration reference are in the German [README](README.md) (sections „Lokale Entwicklung“ and „Prüfung und Release“).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

[GNU Affero General Public License v3.0 or later](LICENSE). If you run a modified version as a network service, you must offer its source code to its users.

### Continuous collection and training controls

With learning enabled, imported images enter the review queue even without a model or ROI. Admins can upload images, choose automatic or manual training, and set automatic-image retention to 1–365 days (default seven). Confirmed positive/negative examples and uploads are retained. The latest camera preview stays available. Background updates announce new images without replacing the image being edited. Back up PostgreSQL and both image volumes before migration 0011; the new retention policy also applies to existing unreviewed automatic images.

## Updating an existing installation to 0.7.0

Follow the [upgrade guide](docs/UPDATE.md) before restarting. Keep the existing `.env`, encryption key, Compose project and volumes. Back up the database and both image stores while backend and worker are stopped. Pin `FILLENS_VERSION=0.7.0` for prebuilt images; source installations check out `v0.7.0` and rebuild. 0.7.0 adds no migration; from 0.5.2, migrations 0009–0011 run automatically. Existing accounts, camera assignments, confirmed examples and models are preserved; unreviewed automatic images use seven-day retention by default. A rollback requires the matching complete backup.
