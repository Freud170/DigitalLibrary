import re

from sqlalchemy import select

from digitallibrary.models import Buch, Medium, Spiel, Status
from tests.conftest import DUNE_ISBN

BUCH_FORMULAR = {
    "titel": "Der Wüstenplanet",
    "isbn": DUNE_ISBN,
    "autoren": "Frank Herbert",
    "verlag": "Heyne Verlag",
    "jahr": "2016",
    "seiten": "800",
    "cover_url": "https://covers.openlibrary.org/b/id/15247677-M.jpg",
    "openlibrary_key": "/books/OL32663508M",
}


def _buch_speichern(client, **aenderungen) -> int:
    antwort = client.post("/buecher", data={**BUCH_FORMULAR, **aenderungen})
    assert antwort.status_code == 303, antwort.text
    return int(antwort.headers["location"].rsplit("/", 1)[1])


def test_start_leitet_auf_buecher(client):
    antwort = client.get("/")
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/buecher"


def test_leere_listen(client):
    assert "Noch keine Bücher" in client.get("/buecher").text
    assert "Steam-Import kommt" in client.get("/spiele").text


def test_isbn_suche_fuellt_formular(client, anfragen):
    antwort = client.post("/buecher/suche", data={"isbn": "978-3-453-31717-8"})
    assert antwort.status_code == 200
    assert 'value="Der Wüstenplanet"' in antwort.text
    assert 'value="Frank Herbert"' in antwort.text
    assert 'value="800"' in antwort.text
    assert "bitte kurz prüfen" in antwort.text
    assert len(anfragen) == 1


def test_ungueltige_isbn_fragt_open_library_gar_nicht(client, anfragen):
    antwort = client.post("/buecher/suche", data={"isbn": "0000000000000"})
    assert "keine gültige ISBN" in antwort.text
    assert anfragen == []


def test_unbekannte_isbn_bietet_leeres_formular_mit_isbn(client):
    antwort = client.post("/buecher/suche", data={"isbn": "9783999999999"})
    assert "kennt diese ISBN nicht" in antwort.text
    assert 'name="isbn" maxlength="20" value="9783999999999"' in antwort.text


def test_open_library_offline(client, openlibrary_antworten):
    import httpx

    openlibrary_antworten[DUNE_ISBN] = httpx.Response(500)
    antwort = client.post("/buecher/suche", data={"isbn": DUNE_ISBN})
    assert "nicht erreichbar" in antwort.text
    assert 'action="/buecher"' in antwort.text


def test_buch_speichern_und_anzeigen(client, sitzung):
    medium_id = _buch_speichern(client)
    buch = sitzung.get(Buch, medium_id)
    assert buch.titel == "Der Wüstenplanet"
    assert buch.seiten == 800
    assert buch.jahr == 2016
    seite = client.get(f"/medien/{medium_id}").text
    assert "Der Wüstenplanet" in seite
    assert "≈ 20" in seite  # 800 Seiten × 1,5 min = 20 h
    liste = client.get("/buecher").text
    assert "Der Wüstenplanet" in liste
    assert "Frank Herbert" in liste


def test_suche_meldet_vorhandenes_buch(client):
    medium_id = _buch_speichern(client)
    antwort = client.post("/buecher/suche", data={"isbn": "3453317173"})  # gleiche ISBN als ISBN-10
    assert "schon in der Bibliothek" in antwort.text
    assert f"/medien/{medium_id}" in antwort.text


def test_doppeltes_speichern_wird_abgelehnt(client):
    _buch_speichern(client)
    antwort = client.post("/buecher", data=BUCH_FORMULAR)
    assert antwort.status_code == 422
    assert "schon in der Bibliothek" in antwort.text
    assert 'value="Frank Herbert"' in antwort.text  # Eingaben bleiben erhalten


def test_buch_von_hand_ohne_isbn(client, sitzung):
    medium_id = _buch_speichern(client, isbn="", cover_url="", openlibrary_key="", titel="Opas Buch", seiten="")
    buch = sitzung.get(Buch, medium_id)
    assert buch.isbn13 is None
    assert buch.cover_url is None


def test_ungueltige_eingaben_beim_anlegen(client, sitzung):
    for felder, meldung in [
        ({"titel": ""}, "Titel"),
        ({"isbn": "123"}, "ISBN ist ungültig"),
        ({"seiten": "viele"}, "Seiten"),
        ({"jahr": "3000"}, "Jahr"),
        ({"cover_url": "javascript:alert(1)"}, "https://"),
    ]:
        antwort = client.post("/buecher", data={**BUCH_FORMULAR, **felder})
        assert antwort.status_code == 422, felder
        assert meldung in antwort.text, felder
    assert sitzung.scalars(select(Medium)).all() == []


def test_medium_bearbeiten(client, sitzung):
    medium_id = _buch_speichern(client)
    stimmung_ids = re.findall(r'name="stimmung_ids" value="(\d+)"', client.get(f"/medien/{medium_id}").text)
    antwort = client.post(
        f"/medien/{medium_id}",
        data={
            **BUCH_FORMULAR,
            "status": "laeuft",
            "bewertung": "9",
            "dauer_h": "12,5",
            "notiz": "Sandwürmer!",
            "aktuelle_seite": "120",
            "stimmung_ids": stimmung_ids[:2],
        },
    )
    assert antwort.status_code == 303
    assert antwort.headers["location"] == f"/medien/{medium_id}?gespeichert=1"
    buch = sitzung.get(Buch, medium_id)
    assert buch.status == Status.LAEUFT
    assert buch.begonnen_am is not None
    assert buch.bewertung == 9
    assert buch.dauer_min == 750
    assert buch.notiz == "Sandwürmer!"
    assert buch.aktuelle_seite == 120
    assert [f.wert for f in buch.fortschritt] == [120]
    assert len(buch.stimmungen) == 2
    seite = client.get(antwort.headers["location"]).text
    assert "Gespeichert." in seite
    assert 'value="12,5"' in seite


def test_bearbeiten_mit_fehler_aendert_nichts(client, sitzung):
    medium_id = _buch_speichern(client)
    antwort = client.post(f"/medien/{medium_id}", data={**BUCH_FORMULAR, "bewertung": "11", "notiz": "neu"})
    assert antwort.status_code == 422
    assert "Bewertung" in antwort.text
    sitzung.expire_all()
    assert sitzung.get(Buch, medium_id).notiz == ""


def test_isbn_aendern_auf_vorhandene_wird_abgelehnt(client):
    erstes = _buch_speichern(client)
    zweites = _buch_speichern(client, isbn="", titel="Anderes Buch")
    antwort = client.post(f"/medien/{zweites}", data={**BUCH_FORMULAR, "titel": "Anderes Buch"})
    assert antwort.status_code == 422
    assert "schon in der Bibliothek" in antwort.text
    assert erstes != zweites


def test_status_in_der_liste_aendern(client, sitzung):
    medium_id = _buch_speichern(client)
    antwort = client.post(f"/medien/{medium_id}/status", data={"status": "beendet"})
    assert antwort.status_code == 200
    assert f'id="medium-{medium_id}"' in antwort.text
    assert 'value="beendet" selected' in antwort.text
    buch = sitzung.get(Buch, medium_id)
    assert buch.status == Status.BEENDET
    assert buch.beendet_am is not None
    assert client.post(f"/medien/{medium_id}/status", data={"status": "quatsch"}).status_code == 422


def test_statusfilter(client):
    medium_id = _buch_speichern(client)
    client.post(f"/medien/{medium_id}/status", data={"status": "laeuft"})
    assert "Der Wüstenplanet" in client.get("/buecher?status=laeuft").text
    assert "Keine Einträge" in client.get("/buecher?status=beendet").text
    assert "Der Wüstenplanet" in client.get("/buecher?status=unsinn").text  # unbekannt → alle


def test_stimmung_anlegen(client):
    medium_id = _buch_speichern(client)
    antwort = client.post("/stimmungen", data={"name": "kurzweilig", "zurueck": str(medium_id)})
    assert antwort.status_code == 303
    assert "kurzweilig" in client.get(f"/medien/{medium_id}").text


def test_loeschen(client, sitzung):
    medium_id = _buch_speichern(client)
    antwort = client.post(f"/medien/{medium_id}/loeschen")
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/buecher"
    assert sitzung.get(Medium, medium_id) is None
    assert client.get(f"/medien/{medium_id}").status_code == 404


def test_spiel_wird_im_spiele_tab_angezeigt(client, sitzung):
    sitzung.add(Spiel(titel="Hades", steam_appid=1145360, spielzeit_min=90))
    sitzung.commit()
    liste = client.get("/spiele").text
    assert "Hades" in liste
    assert "1,5 h gespielt" in liste
    assert "Hades" not in client.get("/buecher").text


def test_titel_wird_escaped(client):
    medium_id = _buch_speichern(client, isbn="", titel='<script>alert("x")</script>')
    seite = client.get(f"/medien/{medium_id}").text
    assert "<script>alert" not in seite
    assert "&lt;script&gt;" in seite


def test_htmx_und_stil_werden_ausgeliefert(client):
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/stil.css").status_code == 200
