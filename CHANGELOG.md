# Änderungen

## 1.1.0 – unveröffentlicht (Fork)
Zuverlässigkeit.

- Eigener Cloud-Client (`app/tonie_cloud.py`) statt `tonie-api`: jeder Aufruf mit Timeout, jeder HTTP-Fehler bricht ab
  (vorher wurden Fehler beim Löschen/Anlegen von Kapiteln still verschluckt und als Erfolg verbucht)
- Upload wird geprüft: Der Tonie wird danach neu abgerufen, erst wenn genau die neuen Kapitel drauf sind, gilt er als bespielt
- Erst alle Dateien hochladen, dann die Kapitel ersetzen – bricht der Upload ab, bleibt der alte Inhalt auf dem Tonie
- Folgen, die 3-mal beim Hochladen scheitern, werden ausgesetzt (`MAX_FEHLVERSUCHE`); Anmelde-/Abruffehler zählen nicht
- Kurzer Cloud-Ausfall markiert keine Tonies mehr als „nicht mehr im Konto“
- Uhrzeit wird beim Speichern geprüft; eine kaputte gespeicherte Uhrzeit wird auf 17:30 gesetzt statt den Planer
  für alle Tonies anzuhalten; jeder Tonie wird im Planer einzeln abgesichert
- Port-Fehler behoben: im Container immer 8095, `PORT` gilt nur nach außen
- Weboberfläche nutzt relative Pfade (Vorbereitung für Home-Assistant-Ingress)
- Tests mit nachgebildeter Cloud (`pytest tests`)

## 1.0.0 – 2026-11-13
Erste öffentliche Version.

- Mehrere Kreativ-Tonies mit eigenem Ordner, eigener Uhrzeit, eigenem Plan und Verlauf
- Anzeige, was gerade auf jedem Tonie liegt (Kapitel, belegte/freie Minuten)
- Plan per Ziehen oder ↑↓ umsortieren, Folgen aussetzen, „als Nächstes“, mischen
- Pause bis Datum oder unbefristet, „Jetzt aufspielen“
- Überspringt Folgen über 89 Minuten sowie leere/defekte Dateien
- Automatische Ordnerzuordnung nach Tonie-Namen; Automatik bleibt aus, bis sie eingeschaltet wird
- Einrichtung per `install.sh`, Betrieb als Docker-Container
