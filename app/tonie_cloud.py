# Tonie Management – schlanker Client für die tonies-Cloud. Lizenz: MIT, siehe LICENSE.
# Inoffiziell, nicht von der Boxine GmbH (tonies®). Gleiche Endpunkte wie die Bibliothek „tonie-api“,
# aber: jeder Aufruf hat einen Timeout und jeder HTTP-Fehler bricht mit CloudFehler ab, statt still „{}“ zu liefern.
"""Zugriff auf die tonies-Cloud (Anmelden, Kreativ-Tonies lesen, Dateien hochladen, Kapitel setzen)."""
import mimetypes
import os

import requests

API_URL = "https://api.tonie.cloud/v2"
LOGIN_URL = "https://login.tonies.com/auth/realms/tonies/protocol/openid-connect/token"
TIMEOUT = float(os.environ.get("CLOUD_TIMEOUT", "30"))          # Sekunden je API-Aufruf
UPLOAD_TIMEOUT = float(os.environ.get("UPLOAD_TIMEOUT", "300"))  # Sekunden je Datei-Upload


class CloudFehler(RuntimeError):
    """Die tonies-Cloud hat einen Aufruf abgelehnt, nicht beantwortet oder Unerwartetes geliefert."""


def _pruefen(r, was):
    if not r.ok:
        text = (r.text or "").strip().replace("\n", " ")[:200]
        raise CloudFehler(f"{was}: HTTP {r.status_code}" + (f" – {text}" if text else ""))
    return r


class TonieCloud:
    def __init__(self, user, password, session=None):
        if not user or not password:
            raise CloudFehler("TONIES_USER / TONIES_PASS fehlen.")
        self.s = session or requests.Session()
        try:
            r = self.s.post(LOGIN_URL, timeout=TIMEOUT, data={
                "grant_type": "password", "client_id": "my-tonies", "scope": "openid",
                "username": user, "password": password})
        except requests.RequestException as e:
            raise CloudFehler(f"Anmeldung: {type(e).__name__}") from e
        if r.status_code in (400, 401):
            raise CloudFehler("Anmeldung abgelehnt – E-Mail/Passwort prüfen.")
        token = _pruefen(r, "Anmeldung").json().get("access_token")
        if not token:
            raise CloudFehler("Anmeldung: kein Token erhalten.")
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _req(self, methode, pfad, was, **kw):
        try:
            r = self.s.request(methode, f"{API_URL}/{pfad}", timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            raise CloudFehler(f"{was}: {type(e).__name__}") from e
        _pruefen(r, was)
        try:
            return r.json() if r.content else {}
        except ValueError as e:
            raise CloudFehler(f"{was}: Antwort ist kein JSON") from e

    # ------------------------------------------------------------ lesen
    def households(self):
        hs = self._req("GET", "households", "Haushalte abrufen")
        if not isinstance(hs, list):
            raise CloudFehler("Haushalte abrufen: unerwartete Antwort")
        return hs

    def creative_tonies(self):
        """Alle Kreativ-Tonies aller Haushalte (dicts wie von der Cloud, mit householdId)."""
        out = []
        for h in self.households():
            ts = self._req("GET", f"households/{h['id']}/creativetonies", "Kreativ-Tonies abrufen")
            if not isinstance(ts, list):
                raise CloudFehler("Kreativ-Tonies abrufen: unerwartete Antwort")
            for t in ts:
                t.setdefault("householdId", h["id"])
                out.append(t)
        return out

    def tonie(self, t):
        """Einen Kreativ-Tonie frisch abrufen (t: dict mit id und householdId)."""
        return self._req("GET", f"households/{t['householdId']}/creativetonies/{t['id']}", "Tonie abrufen")

    # ------------------------------------------------------------ schreiben
    def datei_hochladen(self, pfad):
        """Datei in den Cloud-Speicher laden, gibt die fileId zurück (noch kein Kapitel)."""
        anfrage = self._req("POST", "file", "Upload anfordern", json={})
        try:
            file_id, ziel, felder = anfrage["fileId"], anfrage["request"]["url"], anfrage["request"]["fields"]
        except (KeyError, TypeError) as e:
            raise CloudFehler("Upload anfordern: unerwartete Antwort") from e
        mime = mimetypes.guess_type(pfad)[0]
        try:
            with open(pfad, "rb") as fh:
                r = requests.post(ziel, data=felder, files={"file": (felder.get("key", file_id), fh, mime)},
                                  timeout=UPLOAD_TIMEOUT)
        except requests.RequestException as e:
            raise CloudFehler(f"Datei hochladen: {type(e).__name__}") from e
        _pruefen(r, "Datei hochladen")
        return file_id

    def kapitel_leeren(self, t):
        self._req("PATCH", f"households/{t['householdId']}/creativetonies/{t['id']}", "Kapitel löschen",
                  json={"chapters": []})

    def kapitel_anhaengen(self, t, file_id, titel):
        self._req("POST", f"households/{t['householdId']}/creativetonies/{t['id']}/chapters", "Kapitel anlegen",
                  json={"title": titel, "file": file_id})
