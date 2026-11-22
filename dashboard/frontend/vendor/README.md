# Lokale Fremdbibliotheken des Dashboards

Das Dashboard lädt keine Dateien von fremden Servern (kein CDN, kein Google Fonts).
Dadurch funktioniert es ohne Internet, und beim Öffnen gehen keine IP-Adressen
an Dritte (DSGVO). Die Dateien stammen unverändert aus den npm-Paketen:

| Datei | Paket | Lizenz |
|---|---|---|
| `chartjs/chart.umd.min.js` | `chart.js@4.6.1` (`dist/`) | MIT, `chartjs/LICENSE.md` |
| `fonts/dm-sans-latin-{300,400,500}-normal.woff2` | `@fontsource/dm-sans@5.3.0` | SIL OFL 1.1, `fonts/OFL-DM-Sans.txt` |
| `fonts/space-mono-latin-{400,700}-normal.woff2` | `@fontsource/space-mono@5.3.0` | SIL OFL 1.1, `fonts/OFL-Space-Mono.txt` |

Aktualisieren: `npm pack <paket>@<version>`, Datei aus dem Archiv kopieren,
Version in dieser Tabelle anpassen.
