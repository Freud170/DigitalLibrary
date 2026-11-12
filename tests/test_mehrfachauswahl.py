import datetime as dt

from sqlalchemy import select

from digitallibrary import bibliothek as bib
from digitallibrary.models import Buch, Spiel, Status

HEUTE = dt.date(2026, 11, 12)


def _spiele(sitzung, *daten):
    spiele = [
        Spiel(titel=t, steam_appid=i, spielzeit_min=m, zuletzt_gespielt=z)
        for i, (t, m, z) in enumerate(daten, start=1)
    ]
    sitzung.add_all(spiele)
    sitzung.commit()
    return spiele


def test_status_mehrere(sitzung):
    a, b, c = _spiele(sitzung, ("A", 0, None), ("B", 0, None), ("C", 0, None))
    b.status = Status.BEENDET
    sitzung.commit()
    anzahl = bib.status_mehrere(sitzung, [a.id, b.id, 999], Status.BEENDET, heute=HEUTE)
    sitzung.commit()
    assert anzahl == 1  # b war schon beendet, 999 gibt es nicht
    assert a.status == Status.BEENDET
    assert a.beendet_am == HEUTE
    assert c.status == Status.BACKLOG
    assert bib.status_mehrere(sitzung, [], Status.BEENDET) == 0


def test_sortierungen(sitzung):
    _spiele(
        sitzung,
        ("b", 50, dt.datetime(2025, 1, 1)),
        ("a", 500, None),
        ("c", 5, dt.datetime(2026, 1, 1)),
    )
    titel = lambda sort: [m.titel for m in bib.medien_liste(sitzung, Spiel, sortierung=sort)]  # noqa: E731
    assert titel("titel") == ["a", "b", "c"]
    assert titel("spielzeit") == ["a", "b", "c"]
    assert titel("zuletzt") == ["c", "b", "a"]  # nie gespielt ans Ende
    assert titel("unsinn") == ["a", "b", "c"]  # Standard: Status, dann Titel


def test_mehrfachauswahl_ueber_oberflaeche(client, sitzung):
    a, _, c = _spiele(sitzung, ("Alpha", 0, None), ("Beta", 0, None), ("Gamma", 0, None))
    seite = client.get("/spiele").text
    assert 'id="massen"' in seite
    assert f'name="ids" value="{a.id}" form="massen"' in seite

    seite = client.get("/spiele?status=backlog&sort=spielzeit").text
    assert 'name="zurueck" value="/spiele?status=backlog&amp;sort=spielzeit"' in seite
    antwort = client.post(
        "/medien/status",
        data={"ids": [a.id, c.id], "neuer_status": "abgebrochen", "zurueck": "/spiele?status=backlog&sort=spielzeit"},
    )
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/spiele?status=backlog&sort=spielzeit&geaendert=2"
    sitzung.expire_all()
    assert [s.status for s in sitzung.scalars(select(Spiel).order_by(Spiel.titel))] == [
        Status.ABGEBROCHEN, Status.BACKLOG, Status.ABGEBROCHEN
    ]
    seite = client.get(antwort.headers["location"]).text
    assert "Status bei 2 Einträgen geändert." in seite
    assert "Beta" in seite
    assert "Alpha" not in seite  # nicht mehr im Backlog-Filter


def test_mehrfachauswahl_ohne_auswahl_und_fehler(client):
    antwort = client.post("/medien/status", data={"neuer_status": "beendet", "zurueck": "/buecher?ansicht=liste"})
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/buecher?ansicht=liste&geaendert=0"
    assert "Keine Änderung." in client.get(antwort.headers["location"]).text
    # alte Meldung wird ersetzt, nicht angehängt
    antwort = client.post("/medien/status", data={"neuer_status": "beendet", "zurueck": "/spiele?geaendert=5"})
    assert antwort.headers["location"] == "/spiele?geaendert=0"
    assert client.post("/medien/status", data={"neuer_status": "quatsch", "zurueck": "/spiele"}).status_code == 422


def test_ruecksprung_nur_auf_eigene_listen(client, sitzung):
    (a,) = _spiele(sitzung, ("Alpha", 0, None))
    for boese in ("https://example.com/spiele", "//example.com/spiele", "/medien/1", "/spiele/../x", ""):
        antwort = client.post("/medien/status", data={"ids": [a.id], "neuer_status": "beendet", "zurueck": boese})
        assert antwort.status_code == 422, boese
    sitzung.expire_all()
    assert sitzung.get(Spiel, a.id).status == Status.BACKLOG  # nichts geändert


def test_sortierung_in_der_oberflaeche(client, sitzung):
    _spiele(sitzung, ("Wenig", 5, None), ("Viel", 500, None))
    seite = client.get("/spiele?sort=spielzeit").text
    assert seite.index("Viel") < seite.index("Wenig")
    assert '<option value="spielzeit" selected>' in seite
    assert "/spiele?status=beendet&amp;sort=spielzeit" in seite  # Filter-Chips behalten die Sortierung


def test_buecher_haben_keine_spielzeit_sortierung(client, sitzung):
    sitzung.add(Buch(titel="Buch"))
    sitzung.commit()
    seite = client.get("/buecher?ansicht=liste&sort=spielzeit").text
    assert 'value="spielzeit"' not in seite
    assert '<option value="status" selected>' in seite


def test_liste_js_wird_ausgeliefert(client):
    assert "data-alle-sichtbaren" in client.get("/static/liste.js").text
