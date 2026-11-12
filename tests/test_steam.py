import datetime as dt
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from digitallibrary import bibliothek as bib
from digitallibrary.config import Einstellungen
from digitallibrary.models import Spiel, Status
from digitallibrary.steam import (
    ASSETS,
    Erfolge,
    SteamFehler,
    SteamSpiel,
    StoreBilder,
    bilder_auslesen,
    eigene_spiele,
    erfolge,
    erfolge_auslesen,
    spiele_auslesen,
    store_bilder,
)
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


# --- Store-Bilder und Erfolge ---


def test_store_bilder(http_client, anfragen):
    bilder = store_bilder([400, 1145360, 413150], http_client)
    assert bilder[400].cover_url == ASSETS + "steam/apps/400/library_600x900.jpg?t=1745368554"
    assert bilder[1145360].cover_url == ASSETS + "steam/apps/1145360/bbb222/library_capsule.jpg?t=1"
    assert bilder[1145360].header_url == ASSETS + "steam/apps/1145360/aaa111/header.jpg?t=1"
    assert 413150 not in bilder  # keine Store-Seite
    assert "key" not in anfragen[0].url.params  # Store braucht keinen Key
    eingabe = json.loads(anfragen[0].url.params["input_json"])
    assert eingabe["data_request"] == {"include_assets": True}


def test_store_bilder_werden_in_bloecken_abgefragt(http_client, anfragen):
    store_bilder(list(range(1, 251)), http_client)
    assert [len(json.loads(a.url.params["input_json"])["ids"]) for a in anfragen] == [100, 100, 50]


def test_bilder_unsinnige_eintraege():
    assert bilder_auslesen([{"appid": 1, "assets": {"asset_url_format": "x", "header": "../böse"}}, "x", {}]) == {
        1: StoreBilder(cover_url=None, header_url=None)
    }
    assert bilder_auslesen(None) == {}


def test_erfolge(http_client, anfragen):
    werte = erfolge(TEST_KEY, TEST_STEAM_ID, [400, 1145360, 413150], http_client)
    assert werte == {400: Erfolge(15, 15), 1145360: Erfolge(12, 49), 413150: Erfolge(0, 0)}
    params = anfragen[0].url.params
    assert params["appids[0]"] == "400" and params["appids[2]"] == "413150"
    assert int(params["max_achievements"]) >= 1000


def test_erfolge_mehr_erreicht_als_gesamt_wird_gedeckelt():
    assert erfolge_auslesen([{"appid": 1, "total_achievements": 2, "achievements": [{}, {}, {}]}]) == {
        1: Erfolge(2, 2)
    }


def test_import_mit_bildern_und_erfolgen(sitzung):
    bilder = {1: StoreBilder("https://shared.akamai.steamstatic.com/store_item_assets/a.jpg", None)}
    bib.steam_importieren(sitzung, [_spiel(1), _spiel(2)], bilder, {1: Erfolge(3, 10)})
    sitzung.commit()
    eins, zwei = (sitzung.scalar(select(Spiel).where(Spiel.steam_appid == i)) for i in (1, 2))
    assert eins.cover_url.endswith("/a.jpg")
    assert (eins.erfolge_erreicht, eins.erfolge_gesamt, eins.erfolge_prozent) == (3, 10, 30)
    assert zwei.cover_url is None  # keine Store-Seite → gezeichnetes Cover
    assert zwei.erfolge_gesamt is None  # nicht abgefragt
    # zweiter Abgleich: nur Erfolge geändert → zählt als aktualisiert
    ergebnis = bib.steam_importieren(sitzung, [_spiel(1), _spiel(2)], bilder, {1: Erfolge(4, 10)})
    assert (ergebnis.aktualisiert, ergebnis.unveraendert) == (1, 1)


def test_eigenes_cover_wird_nicht_ueberschrieben(sitzung):
    bib.steam_importieren(sitzung, [_spiel(1)])
    spiel = sitzung.scalar(select(Spiel))
    spiel.cover_url = "https://example.com/mein-cover.jpg"
    sitzung.commit()
    bib.steam_importieren(sitzung, [_spiel(1)], {1: StoreBilder(ASSETS + "neu.jpg", ASSETS + "h.jpg")})
    assert spiel.cover_url == "https://example.com/mein-cover.jpg"


def test_ohne_store_antwort_bleiben_bilder(sitzung):
    bib.steam_importieren(sitzung, [_spiel(1)], {1: StoreBilder(ASSETS + "a.jpg", ASSETS + "h.jpg")})
    bib.steam_importieren(sitzung, [_spiel(1)], None, None)  # Store nicht erreichbar
    spiel = sitzung.scalar(select(Spiel))
    assert spiel.cover_url == ASSETS + "a.jpg"
    assert spiel.cover_ersatz_url == ASSETS + "h.jpg"


def test_erfolgs_filter_und_sortierung(sitzung):
    for appid, (erreicht, gesamt) in enumerate([(10, 10), (3, 10), (0, 5), (0, 0), (None, None), (1, 2)], 1):
        sitzung.add(Spiel(titel=f"S{appid}", steam_appid=appid, erfolge_erreicht=erreicht, erfolge_gesamt=gesamt))
    sitzung.commit()
    titel = lambda **k: [s.titel for s in bib.medien_liste(sitzung, Spiel, **k)]  # noqa: E731
    assert titel(erfolge="komplett") == ["S1"]
    assert titel(erfolge="angefangen") == ["S2", "S6"]
    assert titel(erfolge="keine") == ["S3"]
    assert titel(erfolge="ohne") == ["S4"]
    assert titel(sortierung="erfolge") == ["S1", "S6", "S2", "S3", "S4", "S5"]
    assert bib.anzahl_erfolge(sitzung) == {"komplett": 1, "angefangen": 2, "keine": 1, "ohne": 1}


# --- Weboberfläche ---


def test_import_ueber_die_oberflaeche(client):
    antwort = client.post("/spiele/steam-import")
    assert antwort.status_code == 200
    assert "3 neu, 0 aktualisiert, 0 unverändert" in antwort.text
    for name in ("Portal", "Hades", "Stardew Valley"):
        assert name in antwort.text
    assert "30,5 h gespielt" in antwort.text  # Hades 1830 min
    assert "🏆 12/49" in antwort.text
    assert 'class="erfolge komplett"' in antwort.text  # Portal 15/15
    assert "bbb222/library_capsule.jpg" in antwort.text
    assert "1 neu" not in client.post("/spiele/steam-import").text
    assert "0 neu, 0 aktualisiert, 3 unverändert" in client.post("/spiele/steam-import").text


def test_import_wenn_store_und_erfolge_ausfallen(client, steam_store, steam_erfolge):
    steam_store["antwort"] = httpx.Response(500)
    steam_erfolge["antwort"] = httpx.Response(500)
    antwort = client.post("/spiele/steam-import")
    assert "3 neu" in antwort.text
    assert "Cover konnten nicht aktualisiert werden." in antwort.text
    assert "Erfolge konnten nicht abgefragt werden." in antwort.text


def test_erfolgsfilter_in_der_oberflaeche(client):
    client.post("/spiele/steam-import")
    seite = client.get("/spiele?erfolge=komplett").text
    assert "Portal" in seite and "Hades" not in seite
    assert "100 % <span>1</span>" in seite
    assert "/spiele?erfolge=komplett&amp;status=beendet" in seite or "status=beendet&amp;erfolge=komplett" in seite
    assert "Hades" in client.get("/spiele?erfolge=angefangen").text
    assert "Keine Einträge mit diesem Filter" in client.get("/spiele?erfolge=keine").text


def test_gezeichnetes_cover_fuer_spiel_ohne_store(client):
    client.post("/spiele/steam-import")
    seite = client.get("/spiele").text
    karte = seite[seite.index("Stardew Valley") - 1500 : seite.index("Stardew Valley")]
    assert 'class="cover-leer cover-spiel"' in karte


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
