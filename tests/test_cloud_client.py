"""Der Cloud-Client darf keinen Fehler verschlucken und muss jeden Aufruf mit Timeout absetzen."""
import pytest
import requests
import responses

import tonie_cloud as tc

TOKEN = {"access_token": "tok"}
H = f"{tc.API_URL}/households"
CT = f"{tc.API_URL}/households/h1/creativetonies"
TONIE = {"id": "t1", "householdId": "h1"}


@pytest.fixture
def cloud():
    with responses.RequestsMock() as rs:
        rs.post(tc.LOGIN_URL, json=TOKEN)
        yield rs, tc.TonieCloud("a@b.c", "pw")


def test_login_abgelehnt():
    with responses.RequestsMock() as rs:
        rs.post(tc.LOGIN_URL, status=401, json={"error": "invalid_grant"})
        with pytest.raises(tc.CloudFehler, match="abgelehnt"):
            tc.TonieCloud("a@b.c", "falsch")


def test_login_ohne_zugangsdaten():
    with pytest.raises(tc.CloudFehler, match="fehlen"):
        tc.TonieCloud("", "")


def test_jeder_aufruf_hat_timeout(cloud):
    rs, c = cloud
    rs.get(H, json=[{"id": "h1"}])
    rs.get(CT, json=[])
    c.creative_tonies()
    for call in rs.calls:
        assert call.request.req_kwargs.get("timeout"), call.request.url


def test_http_fehler_beim_kapitel_loeschen_bricht_ab(cloud):
    rs, c = cloud
    rs.patch(f"{CT}/t1", status=500, body="kaputt")
    with pytest.raises(tc.CloudFehler, match="Kapitel löschen: HTTP 500"):
        c.kapitel_leeren(TONIE)


def test_http_fehler_beim_kapitel_anlegen_bricht_ab(cloud):
    rs, c = cloud
    rs.post(f"{CT}/t1/chapters", status=422, json={"error": "nope"})
    with pytest.raises(tc.CloudFehler, match="Kapitel anlegen: HTTP 422"):
        c.kapitel_anhaengen(TONIE, "f1", "Titel")


def test_timeout_wird_zu_cloudfehler(cloud):
    rs, c = cloud
    rs.get(H, body=requests.Timeout("zu langsam"))
    with pytest.raises(tc.CloudFehler, match="Timeout"):
        c.creative_tonies()


def test_haushalte_nicht_abrufbar_liefert_keine_leere_liste(cloud):
    rs, c = cloud
    rs.get(H, status=503)
    with pytest.raises(tc.CloudFehler, match="HTTP 503"):
        c.creative_tonies()


def test_upload_s3_fehler(cloud, tmp_path):
    rs, c = cloud
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    rs.post(f"{tc.API_URL}/file", json={"fileId": "f1", "request": {"url": "https://s3.example/up", "fields": {"key": "f1"}}})
    rs.post("https://s3.example/up", status=403)
    with pytest.raises(tc.CloudFehler, match="Datei hochladen: HTTP 403"):
        c.datei_hochladen(str(f))


def test_upload_ok_liefert_file_id(cloud, tmp_path):
    rs, c = cloud
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    rs.post(f"{tc.API_URL}/file", json={"fileId": "f1", "request": {"url": "https://s3.example/up", "fields": {"key": "f1"}}})
    rs.post("https://s3.example/up", status=204)
    assert c.datei_hochladen(str(f)) == "f1"
