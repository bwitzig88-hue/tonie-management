"""Testumgebung: nachgebildete tonies-Cloud, temporärer Hörspiel-Ordner und Stand-Datei."""
import copy
import importlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


class FakeCloud:
    """Verhält sich wie TonieCloud, hält die Kreativ-Tonies im Speicher und protokolliert alle Aufrufe.

    Fehler lassen sich gezielt einschalten:
      fehler["login" | "liste" | "abrufen" | "upload" | "setzen" | "zurueck" | "leeren" | "kapitel"] = Exception
      verschlucke_kapitel = True  → Kapitel anlegen „klappt“, landet aber nicht auf dem Tonie
    """

    def __init__(self):
        self.tonies = {}
        self.aufrufe = []
        self.fehler = {}
        self.verschlucke_kapitel = False
        self.uploads = {}
        self.alle_kapitel = []          # jede Datei, die je auf einem Tonie lag (darf zurückgespielt werden)
        self.umwandlungsfehler = []     # wird als transcodingErrors gemeldet
        self.umwandeln_noch = 0         # so viele Abrufe meldet der Tonie noch transcoding=True …
        self.fehler_nach_umwandlung = []  # … danach erscheinen diese transcodingErrors

    def neuer_tonie(self, name, kapitel=()):
        tid = f"t-{len(self.tonies) + 1}"
        self.tonies[tid] = {"id": tid, "householdId": "h1", "name": name, "imageUrl": None,
                            "secondsPresent": 0, "secondsRemaining": 5400,
                            "chapters": [{"id": f"c{i}", "title": k, "file": "x", "seconds": 60, "transcoding": False}
                                         for i, k in enumerate(kapitel)]}
        self.tonies[tid]["chapters"] = [dict(c, file=f"{tid}-{c['id']}") for c in self.tonies[tid]["chapters"]]
        self.alle_kapitel += self.tonies[tid]["chapters"]
        return tid

    def _ggf_fehler(self, was):
        self.aufrufe.append(was)
        if was in self.fehler:
            raise self.fehler[was]

    # --- Schnittstelle wie TonieCloud
    def creative_tonies(self):
        self._ggf_fehler("liste")
        return copy.deepcopy(list(self.tonies.values()))

    def tonie(self, t):
        self._ggf_fehler("abrufen")
        umwandeln = self.umwandeln_noch > 0
        if umwandeln:
            self.umwandeln_noch -= 1
            if not self.umwandeln_noch:
                self.umwandlungsfehler += self.fehler_nach_umwandlung
        return dict(copy.deepcopy(self.tonies[t["id"]]), transcoding=umwandeln,
                    transcodingErrors=list(self.umwandlungsfehler))

    def datei_hochladen(self, pfad):
        self._ggf_fehler("upload")
        fid = str(uuid.uuid4())
        self.uploads[fid] = pfad
        return fid

    def kapitel_setzen(self, t, kapitel, was="Kapitel tauschen"):
        zurueck = was != "Kapitel tauschen"
        self._ggf_fehler("zurueck" if zurueck else "setzen")
        bekannt = set(self.uploads) | {c["file"] for c in self.alle_kapitel}
        for k in kapitel:
            if k["file"] not in bekannt:
                raise AssertionError("Kapitel mit unbekannter Datei")
        if self.verschlucke_kapitel and not zurueck:
            return {}
        neu = [{"id": k.get("id") or k["file"], "title": k["title"], "file": k["file"],
                "seconds": k.get("seconds", 60), "transcoding": False, "type": "file"} for k in kapitel]
        self.tonies[t["id"]]["chapters"] = neu
        self.alle_kapitel += neu
        return copy.deepcopy(self.tonies[t["id"]])

    def kapitel_leeren(self, t):
        self._ggf_fehler("leeren")
        self.tonies[t["id"]]["chapters"] = []

    def kapitel_anhaengen(self, t, file_id, titel):
        self._ggf_fehler("kapitel")
        if file_id not in self.uploads:
            raise AssertionError("Kapitel mit unbekannter fileId")
        if not self.verschlucke_kapitel:
            self.tonies[t["id"]]["chapters"].append(
                {"id": file_id, "title": titel, "file": file_id, "seconds": 60, "transcoding": True})

    def titel(self, tid):
        return [c["title"] for c in self.tonies[tid]["chapters"]]


@pytest.fixture
def tm(tmp_path, monkeypatch):
    """Frisch geladenes tonie_management-Modul mit temporären Ordnern und nachgebildeter Cloud."""
    root = tmp_path / "hoerspiele"
    root.mkdir()
    monkeypatch.setenv("HOERSPIEL_DIR", str(root))
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "data" / "stand.json"))
    monkeypatch.delenv("UHRZEIT", raising=False)
    sys.modules.pop("tonie_management", None)
    mod = importlib.import_module("tonie_management")
    cloud = FakeCloud()
    mod.FAKE = cloud
    mod.ROOT_PATH = root

    def api():
        if "login" in cloud.fehler:
            cloud.aufrufe.append("login")
            raise cloud.fehler["login"]
        return cloud

    monkeypatch.setattr(mod, "api", api)
    monkeypatch.setattr(mod, "dauer", lambda pfad: 0.0 if os.path.getsize(pfad) == 0 else 600.0)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def folgen_anlegen(tm, ordner, *namen):
    d = tm.ROOT_PATH / ordner
    d.mkdir(exist_ok=True)
    for n in namen:
        (d / f"{n}.mp3").write_bytes(b"ID3-test")
    return d


def tonie_einplanen(tm, name, *folgen, kapitel=("Alt",)):
    """Tonie in der Cloud anlegen, Ordner mit Folgen füllen, abrufen und Automatik einschalten."""
    tid = tm.FAKE.neuer_tonie(name, kapitel)
    folgen_anlegen(tm, name, *folgen)
    tm.cloud_abrufen()
    tm.aendern(tid, lambda t: t.update(aktiv=True))
    return tid
