"""Aufspielen gegen die nachgebildete Cloud: Erfolg, stille Fehler, Abbrüche, Fehlversuche, Planer, Uhrzeit."""
import datetime as dt
import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import folgen_anlegen, tonie_einplanen
from tonie_cloud import CloudFehler


def stand(tm, tid):
    return tm.laden()["tonies"][tid]


def test_erfolg_bucht_folge_und_ersetzt_inhalt(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1", "Folge 2")
    assert tm.aufspielen(tid, force=True) is True
    assert tm.FAKE.titel(tid) == ["Folge 1"]
    t = stand(tm, tid)
    assert t["verlauf"][0]["ok"] is True and t["zuletzt"] == "Folge 1"
    assert t["queue"][0] == "Folge 2"


def test_erst_hochladen_dann_kapitel_ersetzen(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    tm.FAKE.aufrufe.clear()
    tm.aufspielen(tid, force=True)
    a = tm.FAKE.aufrufe
    assert a.index("upload") < a.index("leeren") < a.index("kapitel")


def test_kapitel_verschluckt_wird_als_fehler_erkannt(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    tm.FAKE.verschlucke_kapitel = True
    with pytest.raises(RuntimeError, match="nicht bestätigt"):
        tm.aufspielen(tid, force=True)
    t = stand(tm, tid)
    assert t["verlauf"][0]["ok"] is False
    assert t["zuletzt"] is None and t["queue"][0] == "Folge 1"


def test_upload_abbruch_laesst_alten_inhalt_stehen(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1", kapitel=("Altes Hörspiel",))
    tm.FAKE.fehler["upload"] = CloudFehler("Datei hochladen: HTTP 500")
    with pytest.raises(CloudFehler):
        tm.aufspielen(tid, force=True)
    assert tm.FAKE.titel(tid) == ["Altes Hörspiel"]
    assert "HTTP 500" in stand(tm, tid)["verlauf"][0]["fehler"]


def test_nach_drei_fehlversuchen_wird_folge_ausgesetzt(tm):
    tid = tonie_einplanen(tm, "Drache", "1 Kaputt", "2 Gut")
    tm.FAKE.fehler["upload"] = CloudFehler("Datei hochladen: HTTP 415")
    for i in range(1, tm.MAX_FEHLVERSUCHE + 1):
        with pytest.raises(CloudFehler):
            tm.aufspielen(tid, force=True)
        t = stand(tm, tid)
        if i < tm.MAX_FEHLVERSUCHE:
            assert t["fehlversuche"]["1 Kaputt"] == i and "1 Kaputt" not in t["aus"]
    t = stand(tm, tid)
    assert "1 Kaputt" in t["aus"] and "1 Kaputt" not in t["fehlversuche"]
    assert t["verlauf"][0].get("ausgesetzt") is True
    del tm.FAKE.fehler["upload"]
    assert tm.aufspielen(tid, force=True) is True
    assert tm.FAKE.titel(tid) == ["2 Gut"]


def test_anmeldefehler_zaehlt_nicht_gegen_die_folge(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    tm.FAKE.fehler["login"] = CloudFehler("Anmeldung: Timeout")
    for _ in range(tm.MAX_FEHLVERSUCHE + 1):
        with pytest.raises(CloudFehler):
            tm.aufspielen(tid, force=True)
    t = stand(tm, tid)
    assert t["aus"] == [] and t["fehlversuche"] == {}


def test_wieder_einplanen_setzt_zaehler_zurueck(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    tm.FAKE.fehler["kapitel"] = CloudFehler("Kapitel anlegen: HTTP 500")
    with pytest.raises(CloudFehler):
        tm.aufspielen(tid, force=True)
    assert stand(tm, tid)["fehlversuche"] == {"Folge 1": 1}
    # in der Oberfläche aussetzen und wieder einplanen
    web_post(tm, "/api/aussetzen", {"tonie": tid, "name": "Folge 1"})
    assert stand(tm, tid)["fehlversuche"] == {} and stand(tm, tid)["aus"] == ["Folge 1"]
    web_post(tm, "/api/aussetzen", {"tonie": tid, "name": "Folge 1"})
    assert stand(tm, tid)["aus"] == []


def test_cloud_ausfall_markiert_keinen_tonie_als_fehlend(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    tm.FAKE.fehler["liste"] = CloudFehler("Haushalte abrufen: HTTP 503")
    with pytest.raises(CloudFehler):
        tm.cloud_abrufen()
    assert stand(tm, tid)["fehlt"] is False
    assert tm.CLOUD["fehler"]


def test_planer_ein_kaputter_tonie_haelt_andere_nicht_auf(tm, monkeypatch):
    a = tonie_einplanen(tm, "Drache", "Folge A")
    b = tonie_einplanen(tm, "Eule", "Folge B")
    echt = tm.pausiert_am

    def pausiert(t, d):
        if t["name"] == "Drache":
            raise ValueError("kaputter Stand")
        return echt(t, d)
    monkeypatch.setattr(tm, "pausiert_am", pausiert)
    jetzt = dt.datetime.combine(dt.date.today(), dt.time(17, 45))
    tm.planer_runde({}, now=jetzt)
    assert tm.FAKE.titel(b) == ["Folge B"]
    assert tm.FAKE.titel(a) == ["Alt"]


def test_planer_wartet_nach_fehler_zehn_minuten(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    tm.FAKE.fehler["upload"] = CloudFehler("HTTP 500")
    nv = {}
    jetzt = dt.datetime.combine(dt.date.today(), dt.time(17, 45))
    tm.planer_runde(nv, now=jetzt)
    tm.planer_runde(nv, now=jetzt)
    assert tm.FAKE.aufrufe.count("upload") == 1 and tid in nv


@pytest.mark.parametrize("ein,aus", [("7:05", "07:05"), ("23:59", "23:59"), (" 00:00 ", "00:00")])
def test_uhrzeit_gueltig(tm, ein, aus):
    assert tm.uhrzeit_pruefen(ein) == aus


@pytest.mark.parametrize("ein", ["25:00", "12:60", "abc", "", None, "12", "12:5", "-1:30"])
def test_uhrzeit_ungueltig(tm, ein):
    with pytest.raises(ValueError):
        tm.uhrzeit_pruefen(ein)


def test_kaputte_gespeicherte_uhrzeit_wird_repariert(tm):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    s = tm.laden()
    s["tonies"][tid]["uhrzeit"] = "25:00"
    tm.speichern(s)
    assert stand(tm, tid)["uhrzeit"] == tm.STANDARD_UHRZEIT
    tm.planer_runde({}, now=dt.datetime.combine(dt.date.today(), dt.time(17, 45)))   # darf nicht abstürzen


# ---------------------------------------------------------------- Weboberfläche
@pytest.fixture
def server(tm):
    srv = tm.ThreadingHTTPServer(("127.0.0.1", 0), tm.Web)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tm.SERVER_URL = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()


def web_post(tm, pfad, daten):
    if not hasattr(tm, "SERVER_URL"):
        srv = tm.ThreadingHTTPServer(("127.0.0.1", 0), tm.Web)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tm.SERVER_URL = f"http://127.0.0.1:{srv.server_address[1]}"
    req = urllib.request.Request(tm.SERVER_URL + pfad, data=json.dumps(daten).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_web_lehnt_ungueltige_uhrzeit_ab(tm, server):
    tid = tonie_einplanen(tm, "Drache", "Folge 1")
    code, antwort = web_post(tm, "/api/einstellungen", {"tonie": tid, "uhrzeit": "25:00"})
    assert code == 400 and "Uhrzeit" in antwort["error"]
    assert stand(tm, tid)["uhrzeit"] == "17:30"
    code, _ = web_post(tm, "/api/einstellungen", {"tonie": tid, "uhrzeit": "19:15"})
    assert code == 200 and stand(tm, tid)["uhrzeit"] == "19:15"


def test_oberflaeche_nutzt_relative_pfade(tm):
    """Unter HA-Ingress liegt die Seite unter einem Pfad-Präfix – absolute /api-Aufrufe würden ins Leere gehen."""
    assert "'/api/" not in tm.HTML and '"/api/' not in tm.HTML
    assert "fetch('api/zustand'" in tm.HTML


def test_defekte_datei_wird_nicht_hochgeladen(tm):
    tid = tm.FAKE.neuer_tonie("Drache", ("Alt",))
    d = folgen_anlegen(tm, "Drache", "Gut")
    (d / "Leer.mp3").write_bytes(b"")
    tm.cloud_abrufen()
    tm.aendern(tid, lambda t: t.update(aktiv=True, queue=["Leer", "Gut"]))
    tm.aufspielen(tid, force=True)
    assert tm.FAKE.titel(tid) == ["Gut"]
