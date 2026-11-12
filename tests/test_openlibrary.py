import httpx
import pytest

from digitallibrary.openlibrary import OpenLibraryFehler, buch_suchen, daten_auslesen
from tests.conftest import DUNE_ISBN


def test_echte_antwort_wird_ausgelesen(http_client, anfragen):
    daten = buch_suchen(DUNE_ISBN, http_client)
    assert daten is not None
    assert daten.titel == "Der Wüstenplanet"
    assert daten.autoren == "Frank Herbert"
    assert daten.verlag == "Heyne Verlag"
    assert daten.jahr == 2016  # aus "Jan 11, 2016"
    assert daten.seiten == 800
    assert daten.cover_url == "https://covers.openlibrary.org/b/id/15247677-M.jpg"
    assert daten.openlibrary_key == "/books/OL32663508M"
    params = anfragen[0].url.params
    assert params["bibkeys"] == f"ISBN:{DUNE_ISBN}"
    assert params["jscmd"] == "data"


def test_unbekannte_isbn_gibt_none(http_client):
    # Echte Antwort der Open Library für eine unbekannte ISBN ist {}
    assert buch_suchen("9783999999999", http_client) is None


def test_serverfehler_wird_zu_openlibraryfehler(openlibrary_antworten, http_client):
    openlibrary_antworten[DUNE_ISBN] = httpx.Response(503, text="kaputt")
    with pytest.raises(OpenLibraryFehler):
        buch_suchen(DUNE_ISBN, http_client)


def test_kein_json_wird_zu_openlibraryfehler(openlibrary_antworten, http_client):
    openlibrary_antworten[DUNE_ISBN] = httpx.Response(200, text="<html>Wartung</html>")
    with pytest.raises(OpenLibraryFehler):
        buch_suchen(DUNE_ISBN, http_client)


def test_netzwerkfehler_wird_zu_openlibraryfehler():
    def kaputt(request):
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(OpenLibraryFehler), httpx.Client(transport=httpx.MockTransport(kaputt)) as client:
        buch_suchen(DUNE_ISBN, client)


def test_fremde_cover_url_wird_verworfen():
    antwort = {
        "ISBN:9783999999999": {
            "title": "Test",
            "cover": {"medium": "javascript:alert(1)", "large": "https://example.com/x.jpg"},
        }
    }
    daten = daten_auslesen(antwort, "9783999999999")
    assert daten is not None
    assert daten.cover_url is None


def test_untertitel_und_unsinnige_seitenzahl():
    antwort = {"ISBN:9783999999999": {"title": "Titel", "subtitle": "Untertitel", "number_of_pages": -5}}
    daten = daten_auslesen(antwort, "9783999999999")
    assert daten is not None
    assert daten.titel == "Titel: Untertitel"
    assert daten.seiten is None


def test_eintrag_ohne_titel_gilt_als_unbekannt():
    assert daten_auslesen({"ISBN:9783999999999": {"title": "  "}}, "9783999999999") is None
