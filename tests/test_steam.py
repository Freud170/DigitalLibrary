import datetime as dt

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from digitallibrary import bibliothek as bib
from digitallibrary.config import Einstellungen
from digitallibrary.models import Spiel, Status
from digitallibrary.steam import SteamFehler, SteamSpiel, eigene_spiele, spiele_auslesen
from digitallibrary.web import app_erstellen
from tests.conftest import TEST_KEY, TEST_STEAM_ID

# --- API-Antwort auslesen ---


def test_antwort_wird_ausgelesen(http_client, anfragen):
    spiele = eigene_spiele(TEST_KEY, TEST_STEAM_ID, http_client)
    assert [s.name for s in spiele] == ["Portal", "Hades", "Stardew Valley"]
    portal, hades, stardew = spiele
    assert (portal.appid, portal.spielzeit_min, portal.spielzeit_2wochen_min) == (400, 61, 0)
    assert portal.zuletzt_gespielt == dt.datetime.fromtimestamp(1499021715)
    assert hades.spielzeit_2wochen_min == 95
    assert stardew.zuletzt_gespielt is None  # rtime_last_played = 0
    params = anfragen[0].url.params
    assert params["steamid"] == TEST_STEAM_ID
    assert params["include_appinfo"] == "1"
    assert params["include_played_free_games"] == "1"


def test_privates_profil():
    with pytest.raises(SteamFehler, match="Öffentlich"):
        spiele_auslesen({"response": {}})


def test_leere_bibliothek_ist_kein_fehler():
    assert spiele_auslesen({"response": {"game_count": 0, "games": []}}) == []


def test_kaputte_eintraege_werden_uebersprungen():
    spiele = spiele_auslesen({"response": {"games": [{"name": "ohne appid"}, {"appid": 7}, "quatsch"]}})
    assert [(s.appid, s.name) for s in spiele] == [(7, "Steam-App 7")]


@pytest.mark.parametrize(
    ("antwort", "meldung"),
    [
        (httpx.Response(401, text="<html>Unauthorized</html>"), "API-Key"),
        (httpx.Response(403), "API-Key"),
        (httpx.Response(500), "Fehler 500"),
        (httpx.Response(200, text="kein json"), "Unerwartete"),
    ],
)
def test_fehlerantworten(steam_antwort, http_client, antwort, meldung):
    steam_antwort["antwort"] = antwort
    with pytest.raises(SteamFehler, match=meldung):
        eigene_spiele(TEST_KEY, TEST_STEAM_ID, http_client)


def test_netzwerkfehler_verraet_den_key_nicht():
    def kaputt(request):
        raise httpx.ConnectError(f"offline: {request.url}", request=request)

    with httpx.Client(transport=httpx.MockTransport(kaputt)) as client, pytest.raises(SteamFehler) as info:
        eigene_spiele(TEST_KEY, TEST_STEAM_ID, client)
    assert TEST_KEY not in str(info.value)
    assert info.value.__cause__ is None


# --- Abgleich mit der Datenbank ---


def _spiel(appid=1, name="Spiel", minuten=0, zwei_wochen=0, zuletzt=None):
    return SteamSpiel(appid, name, minuten, zwei_wochen, zuletzt)


def test_erster_import(sitzung):
    ergebnis = bib.steam_importieren(
        sitzung, [_spiel(1, "Alt", 600), _spiel(2, "Aktuell", 300, zwei_wochen=60), _spiel(3, "Neu")]
    )
    sitzung.commit()
    assert (ergebnis.neu, ergebnis.aktualisiert, ergebnis.unveraendert) == (3, 0, 0)
    alt, aktuell, neu = (sitzung.scalar(select(Spiel).where(Spiel.steam_appid == i)) for i in (1, 2, 3))
    assert alt.status == Status.BACKLOG
    assert aktuell.status == Status.LAEUFT
    assert aktuell.begonnen_am == dt.date.today()
    assert alt.cover_url.endswith("/1/library_600x900.jpg")
    assert alt.cover_ersatz_url.endswith("/1/header.jpg")
    assert [f.wert for f in alt.fortschritt] == [600]
    assert neu.fortschritt == []  # nie gespielt → keine Momentaufnahme


def test_zweiter_import_aendert_nur_spielzeit(sitzung):
    bib.steam_importieren(sitzung, [_spiel(1, "Hades", 100), _spiel(2, "Portal", 61)])
    sitzung.commit()
    hades = sitzung.scalar(select(Spiel).where(Spiel.steam_appid == 1))
    # Rico pflegt seine eigenen Daten
    hades.titel = "Hades (mein Titel)"
    hades.status = Status.BEENDET
    hades.bewertung = 9
    sitzung.commit()

    zuletzt = dt.datetime(2026, 11, 12, 20, 0)
    ergebnis = bib.steam_importieren(
        sitzung, [_spiel(1, "Hades", 160, zwei_wochen=60, zuletzt=zuletzt), _spiel(2, "Portal", 61)]
    )
    sitzung.commit()
    assert (ergebnis.neu, ergebnis.aktualisiert, ergebnis.unveraendert) == (0, 1, 1)
    assert hades.titel == "Hades (mein Titel)"
    assert hades.status == Status.BEENDET
    assert hades.bewertung == 9
    assert hades.spielzeit_min == 160
    assert hades.zuletzt_gespielt == zuletzt
    assert [f.wert for f in hades.fortschritt] == [100, 160]


def test_spiele_die_bei_steam_fehlen_bleiben(sitzung):
    bib.steam_importieren(sitzung, [_spiel(1), _spiel(2)])
    bib.steam_importieren(sitzung, [_spiel(1)])
    sitzung.commit()
    assert len(sitzung.scalars(select(Spiel)).all()) == 2


def test_doppelte_appid_in_einer_antwort(sitzung):
    ergebnis = bib.steam_importieren(sitzung, [_spiel(1, minuten=5), _spiel(1, minuten=5)])
    sitzung.commit()
    assert (ergebnis.neu, ergebnis.unveraendert) == (1, 1)


# --- Weboberfläche ---


def test_import_ueber_die_oberflaeche(client):
    antwort = client.post("/spiele/steam-import")
    assert antwort.status_code == 200
    assert "3 neu, 0 aktualisiert, 0 unverändert" in antwort.text
    for name in ("Portal", "Hades", "Stardew Valley"):
        assert name in antwort.text
    assert "30,5 h gespielt" in antwort.text  # Hades 1830 min
    assert "1 neu" not in client.post("/spiele/steam-import").text
    assert "0 neu, 0 aktualisiert, 3 unverändert" in client.post("/spiele/steam-import").text


def test_import_meldet_steam_fehler(client, steam_antwort):
    steam_antwort["antwort"] = {"response": {}}
    antwort = client.post("/spiele/steam-import")
    assert antwort.status_code == 200
    assert "Öffentlich" in antwort.text


def test_import_ohne_key(engine, db_pfad, http_client, anfragen):
    einstellungen = Einstellungen(_env_file=None, datenbank=db_pfad, steam_api_key=None, steam_id=None)
    client = TestClient(app_erstellen(einstellungen, engine=engine, http_client=http_client))
    antwort = client.post("/spiele/steam-import")
    assert "STEAM_API_KEY" in antwort.text
    assert anfragen == []


def test_key_taucht_nie_in_der_seite_auf(client):
    for seite in (client.post("/spiele/steam-import").text, client.get("/spiele").text):
        assert TEST_KEY not in seite
