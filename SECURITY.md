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
