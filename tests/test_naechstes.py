import datetime as dt
import random
from collections import Counter

import pytest
from sqlalchemy import select

from digitallibrary import bibliothek as bib
from digitallibrary.models import Buch, Spiel, Status, Stimmung

JETZT = dt.datetime(2026, 11, 12, 20, 0)


def _stimmung(sitzung, name) -> Stimmung:
    return sitzung.scalar(select(Stimmung).where(Stimmung.name == name))


@pytest.fixture
def bestand(sitzung):
    """Je 3 Spiele und Bücher im Backlog, dazu laufende, beendete und eins mit Stimmung."""
    entspannt = _stimmung(sitzung, "entspannt")
    story = _stimmung(sitzung, "Story")
    medien = {
        "spiel1": Spiel(titel="Stardew Valley", stimmungen=[entspannt]),
        "spiel2": Spiel(titel="Elden Ring"),
        "spiel3": Spiel(titel="Disco Elysium", stimmungen=[story]),
        "buch1": Buch(titel="Der Hobbit", stimmungen=[entspannt, story]),
        "buch2": Buch(titel="Sachbuch"),
        "buch3": Buch(titel="Krimi"),
        "laeuft_spiel": Spiel(titel="Hades", status=Status.LAEUFT, stimmungen=[entspannt]),
        "laeuft_buch": Buch(titel="Dune", status=Status.LAEUFT, seiten=800, aktuelle_seite=120),
        "beendet": Spiel(titel="Portal", status=Status.BEENDET, stimmungen=[entspannt]),
        "abgebrochen": Buch(titel="Langweilig", status=Status.ABGEBROCHEN),
    }
    for m in medien.values():
        m.hinzugefuegt_am = JETZT
    sitzung.add_all(medien.values())
    sitzung.commit()
    return medien


def _titel(medien):
    return sorted(m.titel for m in medien)


def test_ohne_filter(sitzung, bestand):
    v = bib.vorschlaege(sitzung, zufall=random.Random(1), jetzt=JETZT)
    assert v.kandidaten == 6
    assert len(v.neu) == 3
    assert len({m.id for m in v.neu}) == 3  # keine Doppelten
    assert all(m.status == Status.BACKLOG for m in v.neu)
    assert _titel(v.weitermachen) == ["Dune", "Hades"]


def test_nur_spiele(sitzung, bestand):
    v = bib.vorschlaege(sitzung, art="spiel", zufall=random.Random(1), jetzt=JETZT)
    assert _titel(v.neu) == ["Disco Elysium", "Elden Ring", "Stardew Valley"]
    assert _titel(v.weitermachen) == ["Hades"]


def test_stimmung_filtert_streng(sitzung, bestand):
    entspannt = _stimmung(sitzung, "entspannt")
    v = bib.vorschlaege(sitzung, stimmung_id=entspannt.id, jetzt=JETZT)
    # nur Einträge MIT dieser Stimmung; beendetes Portal nie
    assert _titel(v.neu) == ["Der Hobbit", "Stardew Valley"]
    assert v.kandidaten == 2
    assert _titel(v.weitermachen) == ["Hades"]  # Dune hat keine Stimmung → raus


def test_stimmung_und_art(sitzung, bestand):
    story = _stimmung(sitzung, "Story")
    v = bib.vorschlaege(sitzung, art="buch", stimmung_id=story.id, jetzt=JETZT)
    assert _titel(v.neu) == ["Der Hobbit"]
    assert v.weitermachen == []


def test_leerer_topf(sitzung):
    v = bib.vorschlaege(sitzung)
    assert (v.neu, v.weitermachen, v.kandidaten) == ([], [], 0)


def test_zufall_mischt(sitzung, bestand):
    ergebnisse = {
        tuple(m.id for m in bib.vorschlaege(sitzung, zufall=random.Random(i), jetzt=JETZT).neu) for i in range(20)
    }
    assert len(ergebnisse) > 5


def test_gewicht_bevorzugt_lange_wartende(sitzung):
    alt = Buch(titel="Alt", hinzugefuegt_am=JETZT - dt.timedelta(days=360))
    neu = Buch(titel="Neu", hinzugefuegt_am=JETZT)
    sitzung.add_all([alt, neu])
    sitzung.commit()
    assert bib.gewicht(alt, JETZT) == 13
    assert bib.gewicht(neu, JETZT) == 1
    assert bib.gewicht(Buch(hinzugefuegt_am=JETZT - dt.timedelta(days=5000)), JETZT) == 13  # gedeckelt
    zufall = random.Random(0)
    zaehler = Counter(bib.vorschlaege(sitzung, anzahl=1, zufall=zufall, jetzt=JETZT).neu[0].titel for _ in range(400))
    assert zaehler["Alt"] > zaehler["Neu"] * 5  # erwartet ca. 13:1


def test_stimmung_mehrere(sitzung, bestand):
    story = _stimmung(sitzung, "Story")
    ids = [bestand["spiel1"].id, bestand["spiel3"].id, bestand["buch2"].id]
    assert bib.stimmung_mehrere(sitzung, ids, story.id, hinzufuegen=True) == 2  # spiel3 hatte sie schon
    sitzung.commit()
    assert story in bestand["spiel1"].stimmungen
    assert bib.stimmung_mehrere(sitzung, ids, story.id, hinzufuegen=False) == 3
    sitzung.commit()
    assert story not in bestand["spiel3"].stimmungen
    with pytest.raises(bib.Eingabefehler):
        bib.stimmung_mehrere(sitzung, ids, 9999, hinzufuegen=True)


# --- Oberfläche ---


def test_seite_zeigt_vorschlaege(client, bestand):
    seite = client.get("/naechstes").text
    assert "Weitermachen" in seite
    assert "Hades" in seite and "Dune" in seite
    assert "S. 120/800" in seite
    assert seite.count('class="vorschlag"') == 3
    assert "3 von 6 passenden" in seite
    assert "Portal" not in seite and "Langweilig" not in seite


def test_seite_mit_filtern(client, sitzung, bestand):
    entspannt = _stimmung(sitzung, "entspannt")
    seite = client.get(f"/naechstes?art=buch&stimmung={entspannt.id}").text
    assert "Der Hobbit" in seite
    assert "Stardew" not in seite
    assert 'value="buch" checked' in seite
    assert f'<option value="{entspannt.id}" selected>' in seite


def test_keine_treffer_mit_stimmung(client, sitzung, bestand):
    fordernd = _stimmung(sitzung, "fordernd")
    seite = client.get(f"/naechstes?stimmung={fordernd.id}").text
    assert "Nichts im Backlog mit der Stimmung „fordernd“" in seite


def test_unsinnige_parameter(client, bestand):
    seite = client.get("/naechstes?art=quatsch&stimmung=abc").text
    assert "3 von 6 passenden" in seite


def test_starten(client, sitzung, bestand):
    buch = bestand["buch2"]
    antwort = client.post(f"/naechstes/starten/{buch.id}")
    assert antwort.status_code == 200
    assert "Los geht's" in antwort.text
    sitzung.expire_all()
    assert sitzung.get(Buch, buch.id).status == Status.LAEUFT
    assert sitzung.get(Buch, buch.id).begonnen_am is not None
    assert client.post("/naechstes/starten/99999").status_code == 404


def test_stimmung_per_mehrfachauswahl(client, sitzung, bestand):
    story = _stimmung(sitzung, "Story")
    ids = [bestand["spiel1"].id, bestand["spiel2"].id]
    seite = client.get("/spiele").text
    assert 'formaction="/medien/stimmung"' in seite
    antwort = client.post(
        "/medien/stimmung",
        data={"ids": ids, "stimmung_id": story.id, "aktion": "hinzufuegen", "zurueck": "/spiele?sort=titel"},
    )
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/spiele?sort=titel&stimmung_geaendert=2"
    assert "Stimmung bei 2 Einträgen geändert." in client.get(antwort.headers["location"]).text
    sitzung.expire_all()
    assert story in sitzung.get(Spiel, bestand["spiel2"].id).stimmungen
    for falsch in ({"aktion": "loeschen"}, {"stimmung_id": 9999}, {"zurueck": "https://example.com"}):
        daten = {"ids": ids, "stimmung_id": story.id, "aktion": "entfernen", "zurueck": "/spiele", **falsch}
        assert client.post("/medien/stimmung", data=daten).status_code == 422, falsch
