# 🎧 Tonie Management

**Jeden Abend automatisch eine neue Hörspielfolge auf dem Kreativ-Tonie – für mehrere Tonies, mit Weboberfläche.**

Tonie Management läuft als kleiner Docker-Dienst auf einem Rechner, der ohnehin durchläuft (Raspberry Pi, NAS, Heimserver).
Du legst Hörspiele in einen Ordner pro Kreativ-Tonie – zur eingestellten Uhrzeit spielt Tonie Management die nächste Folge auf,
und am nächsten Morgen ist sie auf der Toniebox, sobald der Tonie aufgesetzt wird.

![Weboberfläche von Tonie Management](docs/screenshot.png)

> 🤖 **Von einer KI geschrieben:** Der gesamte Code und diese Anleitung wurden von **Claude**, dem KI-Assistenten von
> [Anthropic](https://www.anthropic.com), geschrieben – im Gespräch mit einem Vater, der seinen Kindern abends neue
> Hörspiele bereitstellen wollte. Der Code wurde im Alltag benutzt und mit nachgebildeten Daten getestet, aber nicht
> von einem Menschen Zeile für Zeile geprüft. Bitte lies ihn, bevor du ihm dein tonies-Passwort anvertraust.

> ⚠️ **Inoffiziell:** Kein Produkt der Boxine GmbH (tonies®). Tonie Management nutzt die inoffizielle Schnittstelle wie die Bibliothek
> [tonie-api](https://github.com/Wilhelmsson177/tonie-api). Ändert tonies seine Schnittstelle, kann es jederzeit
> aufhören zu funktionieren.

---

## Was es kann

- **Mehrere Kreativ-Tonies** – jeder hat eigenen Ordner, eigene Uhrzeit, eigenen Plan und Verlauf
- **„Gerade auf dem Tonie“** – zeigt je Tonie die Kapitel, die aktuell in der tonies-Cloud liegen, mit belegten/freien Minuten
- **Plan** – welche Folge an welchem Tag kommt; per Ziehen oder ↑↓ umsortieren, Folgen aussetzen, „als Nächstes“ vorziehen
- **Pause** bis zu einem Datum oder unbefristet (Urlaub, Krankheit …)
- **Jetzt aufspielen** per Knopfdruck
- **Nach jeder Runde** gleiche Reihenfolge oder neu mischen
- **Sicherheitsnetze:** Folgen über 89 Minuten (ein Kreativ-Tonie fasst ca. 90) und leere/defekte Dateien werden übersprungen;
  verpasste Termine (Rechner war aus) werden bis 3 Stunden später nachgeholt
- **Nichts wird ungefragt überschrieben:** Die Automatik ist für jeden Tonie aus, bis du sie einschaltest

## Wie es funktioniert

```
Hörspiel-Ordner/
├── Drache/                         ← Ordner = Name des Kreativ-Tonies (wird automatisch zugeordnet)
│   ├── Der kleine Drache – Folge 1.mp3          ← eine Datei = eine Folge
│   ├── Der kleine Drache – Folge 2.mp3
│   └── Waldabenteuer/                           ← ein Unterordner = eine Folge mit mehreren Kapiteln
│       ├── 01 Aufbruch.mp3
│       └── 02 Die Höhle.mp3
└── Eule/
    └── Sternenhimmel.m4a
```

Zur eingestellten Uhrzeit löscht Tonie Management die bisherigen Kapitel des Tonies in der tonies-Cloud und lädt die
nächste Folge hoch. Die Toniebox holt sie sich beim nächsten Aufsetzen des Tonies (WLAN nötig).

Unterstützte Formate: MP3, M4A, M4B, AAC, OGG, OPUS, WAV, FLAC (umgewandelt wird in der tonies-Cloud).

---

## Voraussetzungen

- Ein Rechner, der abends läuft, mit **Docker** und **Docker Compose v2**
  (getestet auf Raspberry Pi OS; sollte auf jedem Linux und macOS laufen)
  → Docker installieren: <https://docs.docker.com/get-docker/>
  (Raspberry Pi: `curl -fsSL https://get.docker.com | sh` und danach `sudo usermod -aG docker $USER`, neu anmelden)
- Ein **tonies-Konto** (my.tonies.com) mit mindestens einem Kreativ-Tonie
- Hörspiele als Audiodateien, die du nutzen darfst (z. B. eigene CDs, gekaufte Downloads, freie Podcasts)

## Installation

```bash
git clone https://github.com/bestbirchwood/tonie-management.git
cd tonie-management
./install.sh
```

Das Skript fragt einmal nach:

1. **E-Mail und Passwort** deines tonies-Kontos – sie landen nur lokal in der Datei `.env` (Rechte 600)
2. **Hörspiel-Ordner** – z. B. `/srv/hoerspiele` oder ein Ordner auf deinem NAS
3. **Standard-Uhrzeit** – z. B. `17:30`

Danach baut es den Container, startet ihn, listet deine Kreativ-Tonies und legt für jeden einen gleichnamigen Ordner an.
Am Ende steht die Adresse der Weboberfläche, z. B. `http://192.168.1.20:8095`.

**Ohne Skript:** `.env.example` nach `.env` kopieren, ausfüllen, dann `docker compose up -d --build`.

## Erste Schritte

1. Weboberfläche öffnen – oben erscheinen alle Kreativ-Tonies mit ihrem aktuellen Inhalt
2. Hörspiele in den Ordner des gewünschten Tonies legen
3. Tonie anklicken → Plan prüfen → **„Automatik einschalten“**
4. Optional: Uhrzeit je Tonie anpassen, Folgen umsortieren oder aussetzen

Tipp: Lege die Weboberfläche als Lesezeichen auf dem Handy-Homescreen ab.

## Tipps & häufige Fragen

**Ein Tonie bekommt keinen Ordner / „Name doppelt“.**
Haben zwei Kreativ-Tonies denselben Namen (ab Werk oft „Kreativ-Tonie“), ordnet Tonie Management keinen Ordner automatisch zu.
Benenne sie in der tonies-App unterschiedlich und drücke „↻ Tonies abrufen“ (bzw. `./install.sh` erneut, damit die Ordner angelegt werden).

**Ich habe einen Tonie umbenannt.**
Tonies werden an ihrer festen ID erkannt, Plan und Einstellungen bleiben. Liegen im bisherigen Ordner schon Folgen, behält der Tonie
diesen Ordner; ist er leer, wechselt er zum Ordner mit dem neuen Namen. Den Ordner kannst du jederzeit in den Einstellungen ändern.

**Eine Folge ist als „defekt“ markiert.**
Die Datei ist leer (z. B. abgebrochener Kopiervorgang) oder nicht lesbar. Neu kopieren – sie wird dann automatisch wieder eingeplant.

**Ist die Folge sicher auf der Box?**
Tonie Management lädt in die tonies-Cloud. Die Box lädt die Folge erst, wenn der Tonie aufgesetzt wird und die Box im WLAN ist.
Am besten zur Abendzeit bespielen und den Tonie vor dem Schlafengehen einmal kurz aufsetzen.

**Befehle**

```bash
docker exec tonie-management python3 tonie_management.py --liste          # Pläne und aktuellen Inhalt anzeigen
docker exec tonie-management python3 tonie_management.py --jetzt Drache   # sofort die nächste Folge auf „Drache“
docker exec tonie-management python3 tonie_management.py --tonies         # Kreativ-Tonies im Konto anzeigen
docker logs -f tonie-management                                      # Protokoll
```

**Aktualisieren:** `git pull && ./install.sh` – Einstellungen und Pläne bleiben erhalten.

**Deinstallieren:** `docker compose down`, danach den Projektordner löschen (die Hörspiele bleiben, wo sie sind).

## Sicherheit & Datenschutz

- Dein tonies-Passwort steht im Klartext in `.env` auf deinem Rechner. Diese Datei ist in `.gitignore` – **niemals einchecken**.
- Die Weboberfläche hat **keine Anmeldung**. Nur im Heimnetz betreiben, **keine Portfreigabe** im Router einrichten.
  Für Zugriff von unterwegs ein VPN nutzen (z. B. das der FRITZ!Box, WireGuard oder Tailscale).
- Tonie Management spricht nur mit der tonies-Cloud (`app/tonie_cloud.py`). Es sendet keine Nutzungsdaten irgendwohin.
- Der Hörspiel-Ordner wird nur lesend eingebunden; Tonie Management verändert oder löscht dort nichts.
- Gespeichert wird nur `data/stand.json` (Pläne, Verlauf, zuletzt abgerufener Tonie-Inhalt) und `data/dauer.json` (Laufzeiten-Cache).

## Einstellungen (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `TONIES_USER`, `TONIES_PASS` | – | Zugangsdaten des tonies-Kontos |
| `HOERSPIEL_ORDNER` | `./hoerspiele` | Ordner mit den Hörspielen (ein Unterordner je Tonie) |
| `UHRZEIT` | `17:30` | Standard-Uhrzeit für neu erkannte Tonies |
| `MODUS` | `reihenfolge` | `reihenfolge` oder `zufall` (Standard für neue Tonies) |
| `PORT` | `8095` | Port der Weboberfläche |
| `TZ` | `Europe/Berlin` | Zeitzone |
| `MAX_MINUTEN` | `89` | Längere Folgen werden übersprungen |

## Projektaufbau

```
app/tonie_management.py   Dienst: Zeitplan, Upload, Weboberfläche
app/tonie_cloud.py        Client für die tonies-Cloud (Timeouts, Fehler brechen ab)
tests/                    Tests mit nachgebildeter Cloud
Dockerfile           Container auf Basis von python:3.12-slim
docker-compose.yml   Start, Ordner-Einbindung, Port
install.sh           Einrichtung/Aktualisierung
.env.example         Vorlage für die Einstellungen
THIRD_PARTY.md       Verwendete Bibliotheken und ihre Lizenzen
```

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install requests mutagen pytest responses
.venv/bin/python -m pytest -q tests
```

Die Tests laufen gegen eine nachgebildete tonies-Cloud (`tests/conftest.py`), nicht gegen das echte Konto.

## Danksagung & verwendete Bibliotheken

- **[tonie-api](https://github.com/Wilhelmsson177/tonie-api)** von Willi Meierhof (MIT) – ohne diese Bibliothek gäbe es dieses Projekt nicht
- **[mutagen](https://github.com/quodlibet/mutagen)** (GPL-2.0-or-later) – liest die Laufzeit der Audiodateien
- **[Python](https://www.python.org)** und das offizielle **[python](https://hub.docker.com/_/python)**-Docker-Image
- Ähnliches Projekt mit anderem Schwerpunkt (Podcasts): [tonie-podcast-sync](https://pypi.org/project/tonie-podcast-sync/)

Details zu Lizenzen: [THIRD_PARTY.md](THIRD_PARTY.md)

## Lizenz

MIT – siehe [LICENSE](LICENSE). Nutzung auf eigene Gefahr.

---

## English summary

**Tonie Management** automatically puts the next audio-play episode onto your **Creative-Tonies** every evening,
from one folder per Tonie, and gives you a small web UI to see what's currently on each Tonie, reorder the plan, skip
episodes, pause, or upload right now. It runs as a Docker container (Raspberry Pi, NAS, home server).

Install: `git clone …`, `cd tonie-management`, `./install.sh` (asks for your tonies account, the audio folder and a time).
Then open `http://<host>:8095`, drop audio files into the folder named after a Tonie and switch on "Automatik".

The code and docs were **written by Claude, Anthropic's AI assistant**. Unofficial – not affiliated with Boxine GmbH (tonies®);
it relies on the unofficial [tonie-api](https://github.com/Wilhelmsson177/tonie-api) library (MIT) and
[mutagen](https://github.com/quodlibet/mutagen) (GPL-2.0-or-later). Keep the web UI inside your home network – it has no login.
MIT licensed.
