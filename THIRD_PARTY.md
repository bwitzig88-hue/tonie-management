# Verwendete Software / Third-party software

Tonie Management selbst steht unter der MIT-Lizenz (siehe `LICENSE`). Es enthält **keinen** fremden Code;
die folgenden Bibliotheken werden beim Bauen des Docker-Images aus dem Python Package Index (PyPI)
geladen und behalten ihre eigenen Lizenzen.

| Software | Wofür | Lizenz | Quelle |
|---|---|---|---|
| **requests** (Python Software Foundation) | HTTP-Aufrufe an die tonies-Cloud | Apache-2.0 | https://pypi.org/project/requests/ |
| **mutagen** (quodlibet-Projekt) | Laufzeit der Audiodateien lesen (für den 90-Minuten-Check und die Anzeige) | GPL-2.0-or-later | https://github.com/quodlibet/mutagen · https://pypi.org/project/mutagen/ |
| **Python 3.12** inkl. Standardbibliothek (`http.server`, `json`, `threading` …) | Laufzeitumgebung, Weboberfläche | PSF License | https://www.python.org |
| **python:3.12-slim** (Docker Official Image) | Basis-Image des Containers | verschiedene (Debian, PSF) | https://hub.docker.com/_/python |

Die Cloud-Endpunkte in `app/tonie_cloud.py` folgen der Bibliothek **tonie-api** von Willi Meierhof
(MIT, https://github.com/Wilhelmsson177/tonie-api); deren Code ist nicht enthalten.

Hinweis zu mutagen: mutagen steht unter der GPL. Tonie Management bindet es nur zur Laufzeit ein und
verteilt es nicht mit; wer ein fertiges Docker-Image weitergibt, verteilt damit auch mutagen und
muss dessen Lizenzbedingungen beachten.

## Marken / Trademarks

„tonies“, „Toniebox“, „Tonie“ und „Kreativ-Tonie“ sind Marken der Boxine GmbH. Dieses Projekt ist
**inoffiziell** und steht in keiner Verbindung zur Boxine GmbH. Es nutzt eine inoffizielle,
nicht dokumentierte Schnittstelle, die sich jederzeit ändern kann.
