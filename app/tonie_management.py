#!/usr/bin/env python3
# Tonie Management – geschrieben von Claude (KI-Assistent von Anthropic). Lizenz: MIT, siehe LICENSE.
# Inoffiziell, nicht von der Boxine GmbH (tonies®). Spricht die inoffizielle tonies-Cloud-Schnittstelle an (tonie_cloud.py).
"""Tonie Management: bespielt Kreativ-Tonies abends automatisch mit der nächsten Hörspielfolge aus einem Ordner.
Mehrere Kreativ-Tonies: jeder hat seinen eigenen Ordner, Plan, Uhrzeit und Verlauf.
Weboberfläche (Port 8095): Tonie auswählen, sehen was drauf ist, Plan ändern, aussetzen, pausieren.

Ordner (im Container /hoerspiele):
  /hoerspiele/<Name des Tonies>/   ← Folgen für diesen Tonie (wird automatisch zugeordnet)
  - jede Audiodatei (mp3, m4a, …) ist eine Folge
  - jeder Unterordner ist eine Folge; alle Audiodateien darin werden (sortiert) zu Kapiteln
  Alte Einrichtung (Folgen direkt in /hoerspiele) läuft unverändert weiter („Hauptordner“).

  python3 tonie_management.py                -> Dienst + Weboberfläche
  python3 tonie_management.py --jetzt NAME   -> sofort die nächste Folge auf den Tonie NAME spielen
  python3 tonie_management.py --liste        -> Pläne anzeigen
  python3 tonie_management.py --tonies       -> Kreativ-Tonies im Konto anzeigen
"""
import copy
import datetime as dt
import json
import os
import random
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tonie_cloud import CloudFehler, TonieCloud

ROOT = os.environ.get("HOERSPIEL_DIR", "/hoerspiele")
ANZEIGE = os.environ.get("ORDNER_ANZEIGE", ROOT)          # Pfad, wie er auf dem Gastrechner heißt (nur zur Anzeige)
STATE = os.environ.get("STATE_FILE", "/data/stand.json")
CACHE = os.path.join(os.path.dirname(STATE) or ".", "dauer.json")
SICHERUNG = os.path.join(os.path.dirname(STATE) or ".", "sicherung")   # Kapitellisten vor jedem Aufspielen
SICHERUNGEN_JE_TONIE = 10
UMWANDLUNG_WARTEN = int(os.environ.get("UMWANDLUNG_WARTEN", "180"))   # s, so lange auf das Umwandeln der Cloud warten
PORT = int(os.environ.get("PORT", "8095"))
TONIE_NAME = os.environ.get("TONIE_NAME", "").strip()      # nur noch für die Übernahme alter Einstellungen
MAX_MIN = float(os.environ.get("MAX_MINUTEN", "89"))       # Kreativ-Tonie fasst ca. 90 Minuten
AUDIO = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".m4b")
HAUPT = "/"                                                # Folgen direkt im Hauptordner
CLOUD_ALLE_STUNDEN = 6
MAX_FEHLVERSUCHE = int(os.environ.get("MAX_FEHLVERSUCHE", "3"))   # danach wird eine Folge ausgesetzt
STANDARD_UHRZEIT = "17:30"
LOCK = threading.RLock()
RUN = {"busy": None, "msg": ""}                            # busy = ID des Tonies, der gerade bespielt wird
CLOUD = {"fehler": None}


def log(msg):
    print(time.strftime("[%d.%m.%Y %H:%M:%S] ") + msg, flush=True)


def uhrzeit_pruefen(s):
    """'7:05' wird zu '07:05'. Wirft ValueError bei allem, was keine gültige Uhrzeit ist."""
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s or ""))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        raise ValueError(f"Ungültige Uhrzeit „{s}“ – bitte im Format HH:MM (00:00 bis 23:59).")
    return f"{int(m[1]):02d}:{int(m[2]):02d}"


def _start_uhrzeit():
    try:
        return uhrzeit_pruefen(os.environ.get("UHRZEIT", STANDARD_UHRZEIT))
    except ValueError:
        return STANDARD_UHRZEIT


def ordnername(name):
    """Ordnername zu einem Tonie-Namen (so legt ihn auch das Einrichtungsskript an)."""
    return (name or "").replace("/", "-").replace(":", "-").strip() or "Tonie"


# ---------------------------------------------------------------- Folgen & Dauer
_dcache = None


def dauer(path):
    global _dcache
    if _dcache is None:
        try:
            with open(CACHE, encoding="utf-8") as f:
                _dcache = json.load(f)
        except (OSError, ValueError):
            _dcache = {}
    try:
        st = os.stat(path)
    except OSError:
        return 0.0
    key = f"{path}|{int(st.st_mtime)}|{st.st_size}"
    if key in _dcache:
        return _dcache[key]
    sec = 0.0
    try:
        from mutagen import File
        m = File(path)
        sec = float(m.info.length) if m and m.info else 0.0
    except Exception:  # noqa
        pass
    _dcache[key] = sec
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(_dcache, f)
    except OSError:
        pass
    return sec


def unterordner():
    if not os.path.isdir(ROOT):
        return []
    return sorted((e for e in os.listdir(ROOT)
                   if not e.startswith((".", "_")) and os.path.isdir(os.path.join(ROOT, e))), key=str.casefold)


def folgen(ordner, ausnehmen=()):
    """{name: [dateien]} aller Folgen in einem Ordner."""
    base = ROOT if ordner == HAUPT else os.path.join(ROOT, ordner)
    out = {}
    if not os.path.isdir(base):
        return out
    for e in sorted(os.listdir(base), key=str.casefold):
        if e.startswith((".", "_")) or e in ausnehmen:
            continue
        p = os.path.join(base, e)
        if os.path.isfile(p) and e.lower().endswith(AUDIO):
            out[os.path.splitext(e)[0]] = [p]
        elif os.path.isdir(p):
            files = []
            for dp, _, fs in os.walk(p):
                files += [os.path.join(dp, f) for f in fs if f.lower().endswith(AUDIO) and not f.startswith(".")]
            if files:
                out[e] = sorted(files, key=str.casefold)
    return out


# ---------------------------------------------------------------- Stand
TONIE = {"name": "", "bild": None, "inhalt": [], "belegt_s": 0, "frei_s": None, "abgerufen": None, "fehlt": False,
         "ordner": None, "aktiv": False, "queue": [], "aus": [], "runde": [], "verlauf": [], "pause_bis": None,
         "uhrzeit": _start_uhrzeit(), "modus": os.environ.get("MODUS", "reihenfolge").lower(),
         "zuletzt": None, "datum": None, "auto": False,
         "fehlversuche": {}}
PLAN_FELDER = ("queue", "aus", "runde", "verlauf", "pause_bis", "uhrzeit", "modus", "zuletzt", "datum")


def laden():
    try:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    if "tonies" not in s:                       # alter Stand (nur ein Tonie) -> übernehmen, sobald Tonies bekannt
        alt = {k: s[k] for k in PLAN_FELDER if k in s}
        s = {"version": 2, "tonies": {}}
        if alt.get("queue") or alt.get("verlauf"):
            s["alt"] = alt
    for t in s["tonies"].values():
        if "auto" not in t:   # ältere Stände: automatisch zugeordnete Ordner erkennen, Automatik erst mal aus
            t["auto"] = t.get("ordner") not in (None, HAUPT) and t.get("ordner") == ordnername(t.get("name"))
            if t["auto"] and not t.get("verlauf"):
                t["aktiv"] = False
        for k, v in TONIE.items():
            t.setdefault(k, copy.deepcopy(v))
        try:
            t["uhrzeit"] = uhrzeit_pruefen(t["uhrzeit"])
        except ValueError:
            log(f"„{t.get('name')}“: ungültige Uhrzeit „{t['uhrzeit']}“ – {STANDARD_UHRZEIT} verwendet.")
            t["uhrzeit"] = STANDARD_UHRZEIT
    return s


def speichern(s):
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def folgen_fuer(s, tid):
    t = s["tonies"][tid]
    if not t["ordner"]:
        return {}
    if t["ordner"] == HAUPT:  # Ordner anderer Tonies sind keine Folgen
        andere = {x["ordner"] for i, x in s["tonies"].items() if i != tid and x["ordner"] not in (None, HAUPT)}
        return folgen(HAUPT, andere)
    return folgen(t["ordner"])


def abgleichen(t, fl):
    """Neue Dateien hinten anhängen, verschwundene entfernen."""
    t["queue"] = [n for n in t["queue"] if n in fl] + [n for n in fl if n not in t["queue"]]
    t["aus"] = [n for n in t["aus"] if n in fl]
    t["runde"] = [n for n in t["runde"] if n in fl]


def pausiert_am(t, d):
    p = t.get("pause_bis")
    return bool(p) and (p == "immer" or d.isoformat() <= p)


def naechster_termin(t, now=None):
    if not t["aktiv"] or not t["ordner"]:
        return None
    now = now or dt.datetime.now()
    h, m = (int(x) for x in t["uhrzeit"].split(":"))
    d = now.date()
    if t.get("datum") == d.isoformat() or now.time() >= dt.time(h, m):
        d += dt.timedelta(days=1)
    for _ in range(400):
        if not pausiert_am(t, d):
            return dt.datetime.combine(d, dt.time(h, m))
        d += dt.timedelta(days=1)
    return None


def plan(t, fl):
    """Liste mit Termin je Folge (in Queue-Reihenfolge)."""
    nt = naechster_termin(t)
    rows = []
    for n in t["queue"]:
        minuten = sum(dauer(f) for f in fl[n]) / 60
        zu_lang = minuten > MAX_MIN
        defekt = all(dauer(f) <= 0 for f in fl[n])        # leer/abgebrochen kopiert oder nicht lesbar
        row = {"name": n, "dateien": len(fl[n]), "minuten": round(minuten), "aus": n in t["aus"],
               "zu_lang": zu_lang, "defekt": defekt, "termin": None,
               "fehlversuche": t.get("fehlversuche", {}).get(n, 0)}
        if nt and not row["aus"] and not zu_lang and not defekt:
            row["termin"] = nt.date().isoformat()
            d = nt.date() + dt.timedelta(days=1)
            while pausiert_am(t, d):
                d += dt.timedelta(days=1)
            nt = dt.datetime.combine(d, nt.time())
        rows.append(row)
    return rows


# ---------------------------------------------------------------- Tonie-Cloud
def api():
    return TonieCloud(os.environ.get("TONIES_USER"), os.environ.get("TONIES_PASS"))


def feld(o, *namen):
    for n in namen:
        v = o.get(n) if isinstance(o, dict) else getattr(o, n, None)
        if v is not None:
            return v
    return None


def tonie_info(t):
    inhalt = [{"titel": str(feld(c, "title", "name") or "?"), "sek": float(feld(c, "seconds", "duration") or 0)}
              for c in (feld(t, "chapters") or [])]
    belegt = feld(t, "secondsPresent", "seconds_present")
    frei = feld(t, "secondsRemaining", "seconds_remaining")
    return {"name": str(feld(t, "name") or "Kreativ-Tonie"), "bild": feld(t, "imageUrl", "image_url"),
            "inhalt": inhalt, "belegt_s": float(belegt if belegt is not None else sum(c["sek"] for c in inhalt)),
            "frei_s": float(frei) if frei is not None else None, "fehlt": False,
            "abgerufen": time.strftime("%Y-%m-%d %H:%M")}


def cloud_abrufen(a=None):
    """Kreativ-Tonies und ihren aktuellen Inhalt aus der Cloud holen.
    Schlägt der Abruf fehl, bleibt der gespeicherte Stand unverändert (kein Tonie wird als „fehlt“ markiert)."""
    try:
        a = a or api()
        ts = a.creative_tonies()
    except Exception as e:  # noqa
        CLOUD["fehler"] = str(e)[:300]
        raise
    CLOUD["fehler"] = None
    with LOCK:
        s = laden()
        ids = set()
        for t in ts:
            tid = str(feld(t, "id"))
            ids.add(tid)
            e = s["tonies"].setdefault(tid, copy.deepcopy(TONIE))
            e.update(tonie_info(t))
        for tid, e in s["tonies"].items():
            e["fehlt"] = tid not in ids
        # Einstellungen der alten Ein-Tonie-Version übernehmen (Folgen bleiben im Hauptordner)
        alt = s.pop("alt", None)
        if alt:
            ziel = [i for i, e in s["tonies"].items() if TONIE_NAME and e["name"].casefold() == TONIE_NAME.casefold()]
            if not ziel and len(ids) == 1:
                ziel = list(ids)
            if ziel:
                s["tonies"][ziel[0]].update(alt, ordner=HAUPT, aktiv=True)
                log(f"Bisherigen Plan auf „{s['tonies'][ziel[0]]['name']}“ übernommen.")
            else:
                s["alt"] = alt
        # Gleichnamigen Unterordner automatisch zuordnen – aber nur bei eindeutigem Namen.
        # Die Automatik bleibt aus, bis sie in der Weboberfläche eingeschaltet wird.
        subs = {d.casefold(): d for d in unterordner()}
        anzahl = {}
        for e in s["tonies"].values():
            if not e["fehlt"]:
                anzahl[ordnername(e["name"]).casefold()] = anzahl.get(ordnername(e["name"]).casefold(), 0) + 1
        for e in s["tonies"].values():
            if e["ordner"] is not None and not e.get("auto"):
                continue                                   # von Hand gewählt: nie ändern
            key = ordnername(e["name"]).casefold()
            ziel = subs.get(key) if anzahl.get(key) == 1 else None
            if e["ordner"] and ziel != e["ordner"]:
                # Tonie umbenannt: bisherigen Ordner samt Plan behalten, wenn schon Folgen darin liegen
                geteilt = sum(1 for x in s["tonies"].values() if x["ordner"] == e["ordner"]) > 1
                if not geteilt and folgen(e["ordner"]):
                    e["auto"] = False
                    log(f"„{e['name']}“ behält seinen Ordner „{e['ordner']}“ samt Plan.")
                    continue
            if ziel != e["ordner"]:
                if e["ordner"] is not None:
                    log(f"„{e['name']}“: leerer Ordner „{e['ordner']}“ passt nicht mehr – Zuordnung aufgehoben.")
                e.update(ordner=ziel, auto=ziel is not None, aktiv=False, queue=[], aus=[], runde=[])
                if ziel:
                    log(f"Ordner „{ziel}“ → „{e['name']}“ zugeordnet (Automatik noch aus).")
        s["cloud_abgerufen"] = time.strftime("%Y-%m-%d %H:%M")
        speichern(s)
    return ts


def verlauf(t, eintrag):
    t["verlauf"] = ([dict(zeit=time.strftime("%Y-%m-%d %H:%M"), **eintrag)] + t["verlauf"])[:60]


def kapitel_sichern(tid, kapitel):
    """Bisherige Kapitelliste ablegen, damit sie sich zurückspielen lässt (die Cloud bietet keinen Audio-Download)."""
    os.makedirs(SICHERUNG, exist_ok=True)
    pfad = os.path.join(SICHERUNG, f"{tid}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump(kapitel, f, ensure_ascii=False, indent=2)
    alte = sorted(x for x in os.listdir(SICHERUNG) if x.startswith(f"{tid}-") and x.endswith(".json"))
    for x in alte[:-SICHERUNGEN_JE_TONIE]:
        os.remove(os.path.join(SICHERUNG, x))
    return pfad


def inhalt_pruefen(a, tonie, titel_soll, versuche=3, pause=3.0):
    """Tonie neu abrufen und prüfen, ob genau die hochgeladenen Kapitel drauf sind (Titel + Reihenfolge, keine IDs –
    die vergibt die Cloud beim Tausch teils neu). Wartet danach, bis die Cloud mit dem Umwandeln fertig ist,
    damit Umwandlungsfehler noch erkannt werden."""
    ist = []
    for i in range(versuche):
        if i:
            time.sleep(pause)
        d = a.tonie(tonie)
        if feld(d, "transcodingErrors"):
            raise RuntimeError(f"Cloud meldet Umwandlungsfehler: {str(feld(d, 'transcodingErrors'))[:150]}")
        ist = [str(feld(c, "title") or "").strip() for c in (feld(d, "chapters") or [])]
        if ist == [x.strip() for x in titel_soll]:
            break
    else:
        ist = None
    if ist is not None:
        frist = time.time() + UMWANDLUNG_WARTEN
        while feld(d, "transcoding") and time.time() < frist:
            time.sleep(10)
            d = a.tonie(tonie)
            if feld(d, "transcodingErrors"):
                raise RuntimeError(f"Cloud meldet Umwandlungsfehler: {str(feld(d, 'transcodingErrors'))[:150]}")
        if feld(d, "transcoding"):
            log(f"  Cloud wandelt nach {UMWANDLUNG_WARTEN} s noch um – Titel stimmen, gilt als erfolgreich.")
        return
    ist = [str(feld(c, "title") or "").strip() for c in (feld(d, "chapters") or [])]
    raise RuntimeError(f"Upload nicht bestätigt: Auf dem Tonie liegen {len(ist)} Kapitel statt {len(titel_soll)}"
                       + (f" ({', '.join(ist[:3])}{' …' if len(ist) > 3 else ''})" if ist else "") + ".")


def aufspielen(tid, force=False):
    """Nächste Folge auf den Tonie spielen. True bei Erfolg, None wenn nichts zu tun war, Exception bei Fehler."""
    with LOCK:
        if RUN["busy"]:
            return None
        RUN.update(busy=tid, msg="Wird aufgespielt …")
    name = None
    upload_begonnen = False
    try:
        with LOCK:
            s = laden()
            t = s["tonies"].get(tid)
            if not t or not t["ordner"]:
                return None
            fl = folgen_fuer(s, tid)
            abgleichen(t, fl)
            heute = dt.date.today()
            if not force and (t.get("datum") == heute.isoformat() or pausiert_am(t, heute) or not t["aktiv"]):
                return None
            kandidaten = [n for n in t["queue"] if n not in t["aus"]
                          and 0 < sum(dauer(f) for f in fl[n]) / 60 <= MAX_MIN]
            if not kandidaten:
                log(f"„{t['name']}“: keine passende Folge vorhanden.")
                return None
            name = kandidaten[0]
            dateien = fl[name]
        a = api()
        tonie = next((x for x in a.creative_tonies() if str(feld(x, "id")) == tid), None)
        if tonie is None:
            raise RuntimeError("Dieser Kreativ-Tonie ist nicht mehr im Konto.")
        log(f"Bespiele „{feld(tonie, 'name')}“ mit „{name}“ ({len(dateien)} Datei(en)) …")
        upload_begonnen = True
        titel = [(name if len(dateien) == 1 else f"{name} – {i:02d}")[:100] for i in range(1, len(dateien) + 1)]
        # Erst alle Dateien in den Cloud-Speicher laden (dauert) – bricht das ab, ist auf dem Tonie nichts verändert.
        file_ids = []
        for i, f in enumerate(dateien, 1):
            RUN["msg"] = f"Lade „{name}“ hoch … ({i}/{len(dateien)})"
            file_ids.append(a.datei_hochladen(f))
            log(f"  ✓ {os.path.basename(f)}")
        # Bisherigen Inhalt sichern, dann in EINEM Schritt tauschen. Schlägt danach etwas fehl, wird zurückgespielt.
        alt = feld(a.tonie(tonie), "chapters") or []
        kapitel_sichern(tid, alt)
        RUN["msg"] = "Kapitel werden getauscht …"
        try:
            try:
                a.kapitel_setzen(tonie, [{"id": fid, "file": fid, "title": tt} for fid, tt in zip(file_ids, titel)])
            except CloudFehler as e:
                log(f"  Tausch in einem Schritt abgelehnt ({e}) – ersetze Kapitel einzeln.")
                a.kapitel_leeren(tonie)
                for fid, tt in zip(file_ids, titel):
                    a.kapitel_anhaengen(tonie, fid, tt)
            RUN["msg"] = "Inhalt wird geprüft …"
            inhalt_pruefen(a, tonie, titel)
        except Exception as e:  # noqa
            try:
                a.kapitel_setzen(tonie, alt, "Alten Inhalt zurückspielen")
                hinweis = "Alter Inhalt wurde zurückgespielt."
            except Exception as e2:  # noqa
                hinweis = f"Zurückspielen fehlgeschlagen ({e2}) – Sicherung liegt in {SICHERUNG}."
            log(f"  {hinweis}")
            raise RuntimeError(f"{e} {hinweis}") from e
        with LOCK:
            s = laden()
            t = s["tonies"][tid]
            abgleichen(t, folgen_fuer(s, tid))
            if name in t["queue"]:
                t["queue"].remove(name)
                t["queue"].append(name)
            t["runde"].append(name)
            aktive = [n for n in t["queue"] if n not in t["aus"]]
            if all(n in t["runde"] for n in aktive):
                t["runde"] = []
                if t["modus"] == "zufall":
                    random.shuffle(t["queue"])
                    if len(t["queue"]) > 1 and t["queue"][0] == name:
                        t["queue"].append(t["queue"].pop(0))
            t.update(zuletzt=name, datum=dt.date.today().isoformat())
            t["fehlversuche"].pop(name, None)
            verlauf(t, {"name": name, "ok": True})
            speichern(s)
        log("Fertig. Die Toniebox lädt die Folge, sobald der Tonie aufgesetzt wird.")
        try:
            RUN["msg"] = "Inhalt wird abgerufen …"
            cloud_abrufen(a)
        except Exception:  # noqa
            pass
        return True
    except Exception as e:  # noqa
        traceback.print_exc()
        with LOCK:
            s = laden()
            if tid in s["tonies"]:
                t = s["tonies"][tid]
                eintrag = {"name": name or "–", "ok": False, "fehler": str(e)[:300]}
                # Nur Fehler beim Hochladen zählen gegen die Folge – Anmelde- oder Abruffehler liegen nicht an ihr.
                if upload_begonnen and name:
                    n = t["fehlversuche"][name] = t["fehlversuche"].get(name, 0) + 1
                    if n >= MAX_FEHLVERSUCHE:
                        t["fehlversuche"].pop(name, None)
                        if name not in t["aus"]:
                            t["aus"].append(name)
                        eintrag["ausgesetzt"] = True
                        eintrag["fehler"] = f"{n}. Fehlversuch – Folge ausgesetzt. {eintrag['fehler']}"[:300]
                        log(f"„{t['name']}“: „{name}“ nach {n} Fehlversuchen ausgesetzt.")
                verlauf(t, eintrag)
                speichern(s)
        raise
    finally:
        RUN.update(busy=None, msg="")


def planer_runde(naechster_versuch, now=None):
    """Ein Durchgang des Planers: jeden fälligen Tonie bespielen."""
    s = laden()
    now = now or dt.datetime.now()
    for tid, t in s["tonies"].items():
        try:                                       # ein Tonie mit Problemen hält die anderen nicht auf
            if not t["aktiv"] or not t["ordner"] or t["fehlt"]:
                continue
            h, m = (int(x) for x in t["uhrzeit"].split(":"))
            faellig = dt.datetime.combine(now.date(), dt.time(h, m))
            # fällig ab Uhrzeit, Nachholen bis 3 Stunden später
            if (faellig <= now < faellig + dt.timedelta(hours=3) and t.get("datum") != now.date().isoformat()
                    and not pausiert_am(t, now.date()) and time.time() >= naechster_versuch.get(tid, 0)):
                try:
                    ok = aufspielen(tid)
                except Exception:  # noqa
                    ok = False
                if ok:
                    naechster_versuch.pop(tid, None)
                else:                              # Fehler oder nichts Passendes: in 10 min erneut
                    naechster_versuch[tid] = time.time() + 600
        except Exception:  # noqa
            log(f"Planer: Fehler bei „{t.get('name')}“:")
            traceback.print_exc()


def planer():
    log(f"Tonie Management läuft – Weboberfläche auf Port {PORT}.")
    naechster_versuch = {}
    letzter_abruf = 0.0
    while True:
        try:
            if time.time() - letzter_abruf > CLOUD_ALLE_STUNDEN * 3600:
                letzter_abruf = time.time()
                try:
                    cloud_abrufen()
                except Exception as e:  # noqa
                    log(f"Tonie-Cloud nicht erreichbar: {e}")
                    letzter_abruf = time.time() - CLOUD_ALLE_STUNDEN * 3600 + 900   # in 15 min erneut
            planer_runde(naechster_versuch)
        except Exception:  # noqa
            traceback.print_exc()
        time.sleep(30)


# ---------------------------------------------------------------- Weboberfläche
def zustand(tid=None):
    with LOCK:
        s = laden()
        liste = []
        detail = None
        if tid not in s["tonies"]:
            aktive = [i for i, t in s["tonies"].items() if t["aktiv"]]
            tid = (aktive or list(s["tonies"]) or [None])[0]
        for i, t in sorted(s["tonies"].items(), key=lambda x: (x[1]["fehlt"], x[1]["name"].casefold())):
            fl = folgen_fuer(s, i)
            abgleichen(t, fl)
            p = plan(t, fl)
            nx = next((r for r in p if r["termin"]), None)
            liste.append({"id": i, "name": t["name"], "bild": t["bild"], "aktiv": t["aktiv"], "ordner": t["ordner"],
                          "fehlt": t["fehlt"], "uhrzeit": t["uhrzeit"], "pause_bis": t["pause_bis"],
                          "inhalt": [c["titel"] for c in t["inhalt"]], "belegt_min": round(t["belegt_s"] / 60),
                          "doppelt": sum(1 for x in s["tonies"].values() if x["name"] == t["name"] and not x["fehlt"]) > 1,
                          "naechste": nx and {"name": nx["name"], "termin": nx["termin"]}, "folgen": len(p)})
            if i == tid:
                nt = naechster_termin(t)
                detail = {k: t[k] for k in ("name", "bild", "inhalt", "belegt_s", "frei_s", "abgerufen", "fehlt",
                                            "ordner", "aktiv", "uhrzeit", "modus", "pause_bis", "zuletzt", "datum")}
                detail.update(id=i, plan=p, verlauf=t["verlauf"][:15], naechster=nt.isoformat() if nt else None,
                              pfad=ANZEIGE if t["ordner"] == HAUPT else os.path.join(ANZEIGE, t["ordner"] or ordnername(t["name"])))
        speichern(s)
        return {"tonies": liste, "t": detail, "ordner_liste": unterordner(), "wurzel": ANZEIGE,
                "busy": RUN["busy"], "msg": RUN["msg"], "cloud_fehler": CLOUD["fehler"],
                "cloud_abgerufen": s.get("cloud_abgerufen"), "alt_offen": bool(s.get("alt")),
                "max": MAX_MIN, "heute": dt.date.today().isoformat()}


def aendern(tid, fn):
    with LOCK:
        s = laden()
        if tid not in s["tonies"]:
            raise ValueError("Unbekannter Tonie.")
        t = s["tonies"][tid]
        abgleichen(t, folgen_fuer(s, tid))
        fn(t)
        speichern(s)


class Web(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self.send(200, HTML, "text/html; charset=utf-8")
        if u.path == "/api/zustand":
            tid = (parse_qs(u.query).get("t") or [None])[0]
            return self.send(200, json.dumps(zustand(tid), ensure_ascii=False))
        self.send(404, "{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            b = {}
        p, tid = self.path, b.get("tonie")
        try:
            if p == "/api/reihenfolge":
                neu = [x for x in b.get("queue", []) if isinstance(x, str)]
                aendern(tid, lambda t: t.update(queue=[x for x in neu if x in t["queue"]] +
                                                [x for x in t["queue"] if x not in neu]))
            elif p == "/api/aussetzen":
                nm = b.get("name")

                def f(t):
                    t["fehlversuche"].pop(nm, None)
                    t["aus"].remove(nm) if nm in t["aus"] else t["aus"].append(nm)
                aendern(tid, f)
            elif p == "/api/naechste":
                nm = b.get("name")
                aendern(tid, lambda t: t.update(queue=[nm] + [x for x in t["queue"] if x != nm])
                        if nm in t["queue"] else None)
            elif p == "/api/mischen":
                aendern(tid, lambda t: random.shuffle(t["queue"]))
            elif p == "/api/pause":
                bis = b.get("bis")
                if bis not in (None, "immer"):
                    dt.date.fromisoformat(bis)
                aendern(tid, lambda t: t.update(pause_bis=bis))
            elif p == "/api/einstellungen":
                uz, mo = b.get("uhrzeit"), b.get("modus")
                ordner, aktiv = b.get("ordner", "unverändert"), b.get("aktiv")
                neue_uhrzeit = uhrzeit_pruefen(uz) if uz else None      # vor dem Speichern prüfen
                if ordner not in ("unverändert", None, HAUPT) and ordner not in unterordner():
                    raise ValueError("Diesen Ordner gibt es nicht.")

                def f(t):
                    if uz:
                        t["uhrzeit"] = neue_uhrzeit
                    if mo in ("reihenfolge", "zufall"):
                        t["modus"] = mo
                    if ordner != "unverändert" and ordner != t["ordner"]:
                        t.update(ordner=ordner, queue=[], aus=[], runde=[])
                    if ordner != "unverändert":
                        t["auto"] = False
                    if isinstance(aktiv, bool):
                        t["aktiv"] = aktiv
                    if not t["ordner"]:
                        t["aktiv"] = False
                aendern(tid, f)
            elif p == "/api/tonies":
                cloud_abrufen()
            elif p == "/api/jetzt":
                if RUN["busy"]:
                    return self.send(409, json.dumps({"error": "Es wird gerade schon ein Tonie bespielt."}))
                threading.Thread(target=lambda: _sicher(aufspielen, tid, True), daemon=True).start()
                time.sleep(0.3)
            else:
                return self.send(404, "{}")
        except Exception as e:  # noqa
            return self.send(400, json.dumps({"error": str(e)}, ensure_ascii=False))
        self.send(200, json.dumps(zustand(tid), ensure_ascii=False))


def _sicher(fn, *a):
    try:
        fn(*a)
    except Exception:  # noqa
        pass


HTML = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tonie Management</title>
<style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#1f1d1a;--muted:#77716a;--line:#e6e1d8;--accent:#d9480f;--ok:#2f9e44;--warn:#c92a2a;--soft:#fff4e6}
@media (prefers-color-scheme:dark){:root{--bg:#141312;--card:#1d1c1a;--ink:#f1eee8;--muted:#a29c93;--line:#302e2a;--accent:#ff8a4c;--ok:#69db7c;--warn:#ff8787;--soft:#2b2117}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:820px;margin:0 auto;padding:28px 16px 70px}
h1{font-size:1.5rem;margin:0 0 4px;letter-spacing:-.02em}.sub{color:var(--muted);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:16px}
.card h2{font-size:.76rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:0 0 12px}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.tonies{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:18px}
.tn{display:flex;gap:10px;align-items:center;text-align:left;border:2px solid var(--line);background:var(--card);border-radius:14px;padding:10px;width:100%}
.tn.sel{border-color:var(--accent);background:var(--soft)}
.tn.fehlt{opacity:.5}
.tn img,.tn .ph{width:46px;height:46px;border-radius:50%;object-fit:cover;flex:none;background:var(--line);display:grid;place-items:center;font-size:22px}
.tn .t1{font-weight:650;line-height:1.2}.tn .t2{font-size:.78rem;color:var(--muted);line-height:1.3;margin-top:2px;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--muted);margin-right:5px;vertical-align:1px}.dot.on{background:var(--ok)}
.hero{display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.hero .big{font-size:1.15rem;font-weight:650}.hero .when{color:var(--muted)}
.hero .grow{flex:1;min-width:220px}
button{font:inherit;cursor:pointer;border-radius:10px;border:1px solid var(--line);background:none;color:var(--ink);padding:7px 12px}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
button:disabled{opacity:.4;cursor:default}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,select{font:inherit;padding:7px 10px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink);max-width:100%}
.banner{background:var(--soft);border:1px solid var(--accent);border-radius:12px;padding:10px 14px;margin-bottom:16px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.banner.err{border-color:var(--warn)}
.chap{display:grid;grid-template-columns:28px 1fr auto;gap:8px;padding:6px 0;border-top:1px solid var(--line);font-size:.92rem}
.chap:first-child{border-top:0}.chap .no{color:var(--muted);font-variant-numeric:tabular-nums}.chap .d{color:var(--muted);font-variant-numeric:tabular-nums}
.bar{height:8px;border-radius:99px;background:var(--line);overflow:hidden;margin:10px 0 4px}.bar i{display:block;height:100%;background:var(--accent)}
ol{list-style:none;margin:0;padding:0}
li.f{display:grid;grid-template-columns:92px 1fr auto;gap:10px;align-items:center;padding:10px 6px;border-top:1px solid var(--line)}
li.f:first-child{border-top:0}
li.f.drag{opacity:.4}li.f.over{box-shadow:inset 0 3px 0 var(--accent)}
.date{font-variant-numeric:tabular-nums;font-size:.88rem}.date b{display:block;font-size:.95rem}
.date.today b{color:var(--accent)}
.nm{font-weight:550;word-break:break-word}.meta{color:var(--muted);font-size:.8rem}
.off .nm,.off .date{opacity:.45}.off .nm{text-decoration:line-through}
.tag{font-size:.72rem;border-radius:99px;padding:1px 8px;border:1px solid var(--line);color:var(--muted);margin-left:6px}
.tag.w{border-color:var(--warn);color:var(--warn)}
.acts{display:flex;gap:4px}.acts button{padding:5px 8px;font-size:.85rem}
.handle{cursor:grab;color:var(--muted);user-select:none;padding:0 4px}
.hist div{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-top:1px solid var(--line);font-size:.88rem}
.hist div:first-child{border-top:0}.okc{color:var(--ok)}.errc{color:var(--warn)}
.muted{color:var(--muted);font-size:.85rem}.empty{color:var(--muted);padding:8px 0}
code{font-size:.85em;background:var(--bg);padding:1px 5px;border-radius:6px;word-break:break-all}
.grid2{display:grid;grid-template-columns:140px minmax(0,1fr);gap:10px 12px;align-items:center}.grid2>*{min-width:0}.grid2 select{max-width:100%}
@media (max-width:520px){li.f{grid-template-columns:70px 1fr}.acts{grid-column:1/-1;justify-content:flex-end}.handle{display:none}.grid2{grid-template-columns:1fr}}
</style></head><body><main>
<div class="head"><div><h1>🎧 Tonie Management</h1><p class="sub" id="sub"></p></div>
  <button id="abrufen" title="Tonies und ihren Inhalt aus der Tonie-Cloud neu laden">↻ Tonies abrufen</button></div>
<div class="banner err" id="cloudErr" style="display:none"></div>
<div class="tonies" id="tonies"></div>
<div id="detail"></div>
</main>
<script>
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let Z=null, T=null, dragName=null, DIRTY=null, SEL=decodeURIComponent(location.hash.slice(1))||null;
try{ SEL=SEL||localStorage.getItem('tonie'); }catch(e){}
async function api(p,b){const r=await fetch(p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tonie:SEL,...b})}:{});
  const j=await r.json().catch(()=>({})); if(!r.ok){alert(j.error||'Fehler');throw 0;} return j;}
const WT=['So','Mo','Di','Mi','Do','Fr','Sa'];
const mmss=s=>{s=Math.round(s||0);const m=Math.floor(s/60);return m>=60?`${Math.floor(m/60)}:${String(m%60).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`:`${m}:${String(s%60).padStart(2,'0')}`};
function tag(iso){ if(!iso) return ''; const d=new Date(iso+'T12:00'), h=new Date(Z.heute+'T12:00'), diff=Math.round((d-h)/864e5);
  const lab=diff===0?'Heute':diff===1?'Morgen':WT[d.getDay()]; return `<b>${lab}</b> ${d.getDate()}.${d.getMonth()+1}.`; }
const tagKurz=iso=>tag(iso).replace(/<\/?b>/g,'').replace(' ',', ');
const dDE=iso=>new Date(iso+'T12:00').toLocaleDateString('de-DE');
const zeit=v=>new Date(v.replace(' ','T')).toLocaleString('de-DE',{weekday:'short',day:'numeric',month:'numeric',hour:'2-digit',minute:'2-digit'});
const ordnerLabel=o=>o==='/'?'Hauptordner (Folgen direkt im Hörspiel-Ordner)':o;

function render(z){
  Z=z; T=z.t; if(T){ SEL=T.id; try{localStorage.setItem('tonie',SEL)}catch(e){} if(location.hash.slice(1)!==SEL) history.replaceState(null,'','#'+encodeURIComponent(SEL)); }
  $('#sub').textContent=z.tonies.length?`${z.tonies.length} Kreativ-Tonie${z.tonies.length>1?'s':''} · ${z.tonies.filter(t=>t.aktiv).length} automatisch bespielt`+(z.cloud_abgerufen?` · Stand Cloud: ${zeit(z.cloud_abgerufen)}`:''):'';
  const ce=$('#cloudErr'); ce.style.display=z.cloud_fehler?'flex':'none';
  if(z.cloud_fehler) ce.innerHTML=`⚠️ Tonie-Cloud nicht erreichbar: ${esc(z.cloud_fehler)}`;
  $('#abrufen').disabled=!!z.busy;
  $('#tonies').innerHTML=z.tonies.map(t=>`<button class="tn ${t.id===SEL?'sel':''} ${t.fehlt?'fehlt':''}" data-id="${esc(t.id)}">
      ${t.bild?`<img src="${esc(t.bild)}" alt="" onerror="this.outerHTML='<span class=ph>🎧</span>'">`:'<span class="ph">🎧</span>'}
      <span><span class="t1">${esc(t.name)}</span>
      <span class="t2">${t.doppelt?'<span class="errc">⚠︎ Name doppelt</span><br>':''}<span class="dot ${t.aktiv?'on':''}"></span>${z.busy===t.id?'⏳ wird bespielt':t.fehlt?'nicht mehr im Konto':t.aktiv?(t.pause_bis?'pausiert':`täglich ${t.uhrzeit}`):'nicht eingeplant'}<br>
      ${t.inhalt.length?'Drauf: '+esc(t.inhalt[0])+(t.inhalt.length>1?` (+${t.inhalt.length-1})`:''):'leer'}</span></span></button>`).join('')
    || `<div class="card empty">Noch keine Tonies geladen. ${z.cloud_fehler?'':'Klick auf „Tonies abrufen“.'}</div>`;
  document.querySelectorAll('.tn').forEach(b=>b.onclick=()=>{SEL=b.dataset.id; DIRTY=null; tick();});
  renderDetail();
}

function renderDetail(){
  const z=Z, t=T, D=$('#detail');
  if(!t){ D.innerHTML=''; return; }
  const busyHier=z.busy===t.id, frei=t.frei_s, gesamt=(t.belegt_s||0)+(frei||0);
  let h='';
  if(t.pause_bis) h+=`<div class="banner">⏸️ <b>${esc(t.name)} pausiert ${t.pause_bis==='immer'?'bis auf Weiteres':'bis einschließlich '+dDE(t.pause_bis)}</b><button data-pause="">Pause beenden</button></div>`;
  h+=`<section class="card"><h2>Gerade auf „${esc(t.name)}“</h2>
    ${t.inhalt.length?t.inhalt.map((c,i)=>`<div class="chap"><span class="no">${i+1}</span><span>${esc(c.titel)}</span><span class="d">${mmss(c.sek)}</span></div>`).join(''):'<div class="empty">Der Tonie ist leer.</div>'}
    ${gesamt?`<div class="bar"><i style="width:${Math.min(100,100*(t.belegt_s||0)/gesamt)}%"></i></div>
      <div class="muted">${Math.round((t.belegt_s||0)/60)} min belegt${frei!=null?` · ${Math.round(frei/60)} min frei`:''}</div>`:''}
    <div class="muted" style="margin-top:6px">${t.abgerufen?'Stand: '+zeit(t.abgerufen):''}${busyHier?' · ⏳ '+esc(z.msg):''}</div></section>`;
  const doppelt=(z.tonies.find(x=>x.id===t.id)||{}).doppelt;
  if(doppelt) h+=`<div class="banner err">⚠︎ Mehrere Tonies heißen „${esc(t.name)}“. Benenne sie in der Tonies-App (Mein Tonies → Kreativ-Tonie → Name) unterschiedlich und drück dann „↻ Tonies abrufen“ – sonst ist nicht eindeutig, welcher welchen Ordner bekommt.</div>`;
  if(!t.ordner){
    h+=`<section class="card"><h2>Ordner zuordnen</h2>
      <p style="margin-top:0">Für diesen Tonie ist noch kein Ordner festgelegt. Leg einen Ordner <code>${esc(z.wurzel)}/${esc(t.name)}</code> an
      und füll ihn mit Hörspielen – er wird dann automatisch zugeordnet. Oder wähle einen vorhandenen:</p>
      <div class="row"><select id="ordnerNeu"><option value="">– Ordner wählen –</option>${['/',...z.ordner_liste].map(o=>`<option value="${esc(o)}">${esc(ordnerLabel(o))}</option>`).join('')}</select>
      <button class="primary" id="ordnerSet">Zuordnen &amp; einplanen</button></div></section>`;
  } else {
    const nx=t.plan.find(r=>r.termin);
    h+=`<section class="card"><div class="hero">
      <div class="grow"><div class="muted">Als Nächstes auf „${esc(t.name)}“</div><div class="big">${nx?esc(nx.name):(t.aktiv?'Keine Folge geplant':'Automatik ist aus')}</div>
        <div class="when">${nx&&t.naechster?`${tagKurz(nx.termin)} um ${t.uhrzeit} Uhr`:(t.plan.length?'':'Noch keine Folgen im Ordner.')}</div></div>
      <span class="row">${t.aktiv?'':'<button id="anmachen">Automatik einschalten</button>'}
      <button class="primary" id="jetzt" ${z.busy||!t.plan.some(r=>!r.aus&&!r.zu_lang&&!r.defekt)||t.fehlt?'disabled':''}>Jetzt aufspielen</button></span></div>
      ${t.aktiv?'':'<div class="muted" style="margin-top:8px">Solange die Automatik aus ist, bleibt der Tonie, wie er ist – nur „Jetzt aufspielen“ ändert ihn.</div>'}
      ${t.zuletzt?`<div class="muted" style="margin-top:8px">Zuletzt: ${esc(t.zuletzt)} (${dDE(t.datum)})</div>`:''}</section>
    <section class="card"><h2>Plan</h2>
      <div class="row" style="margin-bottom:10px"><button id="mischen">🔀 Mischen</button><span class="muted">Ordner: <code>${esc(t.pfad)}</code></span></div>
      <ol id="list">${t.plan.length?t.plan.map((r,i)=>`<li class="f ${r.aus||r.zu_lang||r.defekt?'off':''}" draggable="true" data-n="${esc(r.name)}">
       <div class="date ${r.termin===z.heute?'today':''}">${r.termin?tag(r.termin):(r.aus?'<b>aus</b>':r.zu_lang?'<b>zu lang</b>':r.defekt?'<b>defekt</b>':(t.aktiv?'<b>Pause</b>':'<b>–</b>'))}</div>
       <div><div class="nm">${esc(r.name)}${r.zu_lang?`<span class="tag w">über ${Math.round(z.max)} min</span>`:''}${r.defekt?'<span class="tag w">Datei leer/defekt – neu kopieren</span>':''}${r.fehlversuche?`<span class="tag w">${r.fehlversuche}× fehlgeschlagen</span>`:''}</div>
         <div class="meta">${r.minuten?r.minuten+' min · ':''}${r.dateien} ${r.dateien===1?'Datei':'Kapitel'}</div></div>
       <div class="acts"><span class="handle">⠿</span>
         <button data-a="up" ${i===0?'disabled':''} title="nach oben">↑</button><button data-a="down" ${i===t.plan.length-1?'disabled':''} title="nach unten">↓</button>
         <button data-a="next" title="als Nächstes">⏭</button><button data-a="aus" title="${r.aus?'wieder einplanen':'aussetzen'}">${r.aus?'▶':'⏸'}</button></div></li>`).join('')
       :`<div class="empty">Noch keine Folgen. Lege MP3-Dateien oder Ordner in <code>${esc(t.pfad)}</code>.</div>`}</ol></section>
    <section class="card"><h2>Einstellungen für „${esc(t.name)}“</h2>
      <div class="grid2">
        <span>Automatisch</span><label class="row"><input type="checkbox" id="aktiv" ${t.aktiv?'checked':''}> jeden Abend bespielen</label>
        <span>Uhrzeit</span><span class="row"><input type="time" id="uhrzeit" value="${esc(t.uhrzeit)}"></span>
        <span>Nach jeder Runde</span><span><select id="modus"><option value="reihenfolge">gleiche Reihenfolge</option><option value="zufall">neu mischen</option></select></span>
        <span>Ordner</span><span><select id="ordner">${['/',...z.ordner_liste].map(o=>`<option value="${esc(o)}" ${o===t.ordner?'selected':''}>${esc(ordnerLabel(o))}</option>`).join('')}</select></span>
        <span></span><span><button class="primary" id="save">Speichern</button></span>
        <span>Pausieren bis</span><span class="row"><input type="date" id="pauseDate"><button id="pauseSet">Pausieren</button>
          <button data-pause="immer">Unbefristet</button>${t.pause_bis?'<button data-pause="">Pause beenden</button>':''}</span>
      </div></section>`;
  }
  h+=`<section class="card"><h2>Zuletzt aufgespielt</h2><div class="hist">${t.verlauf.length?t.verlauf.map(v=>`<div><span>${v.ok?'<span class="okc">✓</span>':'<span class="errc">✗</span>'} ${esc(v.name)}${v.fehler?`<br><span class="muted errc">${esc(v.fehler)}</span>`:''}</span><span class="muted">${zeit(v.zeit)}</span></div>`).join(''):'<div class="muted">Noch nichts aufgespielt.</div>'}</div></section>`;
  const fokus=document.activeElement&&document.activeElement.id;
  if(['uhrzeit','modus','ordner','pauseDate','ordnerNeu'].includes(fokus) || DIRTY===t.id) return;   // ungespeicherte Eingaben nicht überschreiben
  D.innerHTML=h;
  if($('#modus')) $('#modus').value=t.modus;
  D.oninput=D.onchange=e=>{ if(['uhrzeit','modus','ordner','aktiv','ordnerNeu'].includes(e.target.id)) DIRTY=T.id; };
  bind();
}
const order=()=>T.plan.map(r=>r.name);
function bind(){
  document.querySelectorAll('li.f').forEach(li=>{
    const n=li.dataset.n;
    li.querySelectorAll('button[data-a]').forEach(b=>b.onclick=()=>{
      const a=b.dataset.a, q=order(), i=q.indexOf(n);
      if(a==='up'||a==='down'){ const j=a==='up'?i-1:i+1; [q[i],q[j]]=[q[j],q[i]]; api('api/reihenfolge',{queue:q}).then(render); }
      if(a==='next') api('api/naechste',{name:n}).then(render);
      if(a==='aus') api('api/aussetzen',{name:n}).then(render);
    });
    li.ondragstart=e=>{dragName=n; li.classList.add('drag'); e.dataTransfer.effectAllowed='move';};
    li.ondragend=()=>{dragName=null; li.classList.remove('drag'); document.querySelectorAll('.over').forEach(x=>x.classList.remove('over'));};
    li.ondragover=e=>{e.preventDefault(); li.classList.add('over');};
    li.ondragleave=()=>li.classList.remove('over');
    li.ondrop=e=>{e.preventDefault(); if(!dragName||dragName===n) return; const q=order().filter(x=>x!==dragName); q.splice(q.indexOf(n),0,dragName); dragName=null; api('api/reihenfolge',{queue:q}).then(render);};
  });
  document.querySelectorAll('[data-pause]').forEach(b=>b.onclick=()=>api('api/pause',{bis:b.dataset.pause||null}).then(render));
  const on=(id,fn)=>{const e=$('#'+id); if(e) e.onclick=fn;};
  on('jetzt',()=>{ const nx=T.plan.find(r=>!r.aus&&!r.zu_lang&&!r.defekt); if(nx&&confirm(`„${nx.name}“ jetzt auf „${T.name}“ spielen? Was gerade drauf ist, wird ersetzt.`)) api('api/jetzt',{}).then(render); });
  on('mischen',()=>api('api/mischen',{}).then(render));
  on('anmachen',()=>{ if(confirm(`„${T.name}“ ab jetzt jeden Abend um ${T.uhrzeit} Uhr automatisch neu bespielen? Was gerade drauf ist, wird dabei ersetzt.`)) api('api/einstellungen',{aktiv:true}).then(render); });
  on('pauseSet',()=>{ const d=$('#pauseDate').value; if(!d) return alert('Bitte ein Datum wählen.'); api('api/pause',{bis:d}).then(render); });
  on('save',()=>{ const o=$('#ordner').value;
    if(o!==T.ordner && !confirm('Ordner wechseln? Der Plan dieses Tonies beginnt dann neu.')) return;
    DIRTY=null; api('api/einstellungen',{uhrzeit:$('#uhrzeit').value,modus:$('#modus').value,ordner:o,aktiv:$('#aktiv').checked}).then(render); });
  on('ordnerSet',()=>{ const o=$('#ordnerNeu').value; if(!o) return alert('Bitte einen Ordner wählen.'); DIRTY=null; api('api/einstellungen',{ordner:o,aktiv:true}).then(render); });
}
$('#abrufen').onclick=async()=>{ const b=$('#abrufen'); b.disabled=true; b.textContent='⏳ Wird abgerufen …'; try{ render(await api('api/tonies',{})); }catch(e){} b.disabled=false; b.textContent='↻ Tonies abrufen'; };
async function tick(){ try{ const r=await fetch('api/zustand'+(SEL?'?t='+encodeURIComponent(SEL):'')); render(await r.json()); }catch(e){} }
window.onhashchange=()=>{ SEL=decodeURIComponent(location.hash.slice(1))||SEL; tick(); };
tick(); setInterval(()=>{ if(!dragName) tick(); }, 8000);
</script></body></html>"""


def zeige_liste():
    z = zustand()
    for t in z["tonies"]:
        d = zustand(t["id"])["t"]
        print(f"\n■ {d['name']} · Ordner: {d['ordner'] or '–'} · {'täglich ' + d['uhrzeit'] if d['aktiv'] else 'nicht eingeplant'}"
              f" · Pause: {d['pause_bis'] or '–'}")
        print("  Drauf: " + (", ".join(c["titel"] for c in d["inhalt"]) or "leer"))
        for r in d["plan"]:
            when = r["termin"] or ("ausgesetzt" if r["aus"] else "zu lang" if r["zu_lang"] else "DEFEKT" if r["defekt"] else "–")
            print(f"  {when:>10}  {r['name']}  ({r['dateien']} Datei(en), {r['minuten']} min)")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--jetzt":
        cloud_abrufen()
        s = laden()
        wunsch = " ".join(sys.argv[2:]).strip().casefold()
        ids = [i for i, t in s["tonies"].items() if (t["name"].casefold() == wunsch) if wunsch] or \
              [i for i, t in s["tonies"].items() if t["aktiv"]]
        if len(ids) != 1:
            sys.exit("Bitte den Namen des Tonies angeben: " + ", ".join(t["name"] for t in s["tonies"].values()))
        aufspielen(ids[0], force=True)
    elif arg == "--liste":
        try:
            cloud_abrufen()
        except Exception as e:  # noqa
            print(f"(Tonie-Cloud nicht erreichbar: {e})")
        zeige_liste()
    elif arg == "--tonies":
        for t in api().creative_tonies():
            print(f" • {feld(t, 'name')}")
    else:
        threading.Thread(target=planer, daemon=True).start()
        ThreadingHTTPServer(("0.0.0.0", PORT), Web).serve_forever()
