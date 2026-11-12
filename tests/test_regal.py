import datetime as dt
import re

from alembic import command
from sqlalchemy import text

from digitallibrary import bibliothek as bib
from digitallibrary.db import _alembic_config, engine_erstellen, migrieren
from digitallibrary.models import Buch, Status


def _buecher(sitzung, *daten):
    buecher = []
    standard = ("", "", "", None, None)
    for i, eintrag in enumerate(daten):
        werte = eintrag if isinstance(eintrag, tuple) else (eintrag,)
        titel, autoren, reihe, nr, jahr = werte + standard[len(werte) :]
        buecher.append(Buch(titel=titel, autoren=autoren, reihe=reihe, reihe_nr=nr, jahr=jahr,
                            hinzugefuegt_am=dt.datetime(2026, 1, 1) + dt.timedelta(days=i)))  # fmt: skip
    sitzung.add_all(buecher)
    sitzung.commit()
    return buecher


def test_nachname():
    assert bib.nachname("Frank Herbert") == "herbert"
    assert bib.nachname("Frank Herbert, Brian Herbert") == "herbert"
    assert bib.nachname("Ödön von Horváth") == "horvath"
    assert bib.nachname("Homer") == "homer"
    assert bib.nachname("") == ""


def test_sortierungen(sitzung):
    _buecher(
        sitzung,
        ("Dune 2", "Frank Herbert", "Dune", 2, 1969),
        ("Dune", "Frank Herbert", "Dune", 1, 1965),
        ("Der Hobbit", "J. R. R. Tolkien", "", None, 1937),
        ("Ohne Autor",),
        ("Emma", "Jane Austen", "", None, 1815),
    )
    titel = lambda s: [b.titel for b in bib.buecher_sortiert(sitzung, s)]  # noqa: E731
    assert titel("autor") == ["Emma", "Dune", "Dune 2", "Der Hobbit", "Ohne Autor"]
    assert titel("reihe") == ["Dune", "Dune 2", "Der Hobbit", "Emma", "Ohne Autor"]
    assert titel("titel") == ["Der Hobbit", "Dune", "Dune 2", "Emma", "Ohne Autor"]
    assert titel("jahr") == ["Emma", "Der Hobbit", "Dune", "Dune 2", "Ohne Autor"]
    assert titel("neu") == ["Emma", "Ohne Autor", "Der Hobbit", "Dune", "Dune 2"]
    assert titel("quatsch") == titel("titel")


def test_regale_fuellen():
    buecher = [Buch(titel=f"B{i}") for i in range(123)]
    regale = bib.regale_fuellen(buecher)
    assert len(regale) == 20
    assert [r.anzahl for r in regale[:4]] == [50, 50, 23, 0]
    assert [len(b) for b in regale[2].boeden] == [10, 10, 3, 0, 0]
    assert regale[2].boeden[2][0].titel == "B120"
    assert regale[2].fuellstand == 23 / 50


def test_mehr_als_1000_gibt_anbau():
    regale = bib.regale_fuellen([Buch(titel=str(i)) for i in range(1001)])
    assert len(regale) == 21
    assert regale[20].anzahl == 1


def test_regalseite(client, sitzung):
    b = _buecher(sitzung, ("Dune", "Frank Herbert", "Dune", 1, 1965), ("Emma", "Jane Austen"))
    b[0].status = Status.LAEUFT
    b[1].status = Status.BEENDET
    sitzung.commit()
    seite = client.get("/buecher").text
    assert "📚 <strong>2</strong>&nbsp;/&nbsp;1000" in seite  # Zähler oben rechts
    assert "Noch <strong>998</strong> Bücher bis zur offiziellen Bibliothek" in seite
    assert 'id="regal-1"' in seite and 'id="regal-2"' not in seite  # nur benutzte Regale
    assert seite.count('class="platz"') == 48
    assert seite.count('class="lesezeichen"') == 1
    assert seite.count('class="gelesen"') == 1
    assert len(re.findall(r'<a href="#regal-\d+" class="fenster', seite)) == 20  # Gebäude mit 20 Fenstern
    assert seite.count('class="fenster-licht"') == 1
    assert seite.index("Emma") < seite.index("Dune")  # Standard: nach Autor (Austen vor Herbert)
    assert "Leineneinband" not in seite
    assert 'class="cover-leer cover-buch"' in seite  # kein Cover → gezeichneter Einband


def test_regal_sortierung_und_umschalter(client, sitzung):
    _buecher(sitzung, ("Zebra", "Anna Alpha"), ("Apfel", "Zoe Zulu"))
    seite = client.get("/buecher?sort=titel").text
    assert seite.index("Apfel") < seite.index("Zebra")
    assert '<option value="titel" selected>' in seite
    assert 'href="/buecher?ansicht=liste"' in seite
    liste = client.get("/buecher?ansicht=liste").text
    assert 'id="massen"' in liste
    assert 'name="zurueck" value="/buecher?ansicht=liste"' in liste


def test_offizielle_bibliothek(client, sitzung):
    sitzung.add_all([Buch(titel=f"Buch {i}") for i in range(1000)])
    sitzung.commit()
    seite = client.get("/buecher").text
    assert "Offizielle Bibliothek!" in seite
    assert 'class="zaehler offiziell"' in seite
    assert 'class="fahne"' in seite
    assert seite.count('class="fenster-licht"') == 20


def test_reihe_speichern(client, sitzung):
    antwort = client.post("/buecher", data={"titel": "Dune", "reihe": "  Der  Wüstenplanet ", "reihe_nr": "1"})
    assert antwort.status_code == 303
    buch = sitzung.get(Buch, int(antwort.headers["location"].rsplit("/", 1)[1]))
    assert (buch.reihe, buch.reihe_nr) == ("Der Wüstenplanet", 1)
    seite = client.get(f"/medien/{buch.id}").text
    assert 'name="reihe" maxlength="200" value="Der Wüstenplanet"' in seite
    antwort = client.post(f"/medien/{buch.id}", data={"titel": "Dune", "reihe": "", "reihe_nr": "x"})
    assert antwort.status_code == 422
    assert "Band" in antwort.text


def test_migration_von_0001_behaelt_daten(tmp_path):
    pfad = tmp_path / "alt.db"
    engine = engine_erstellen(pfad)
    config = _alembic_config()
    with engine.connect() as v:
        config.attributes["connection"] = v
        command.upgrade(config, "0001")
        v.execute(text("INSERT INTO medien (id, art, titel, status, notiz, hinzugefuegt_am, geaendert_am) "
                       "VALUES (1, 'buch', 'Altes Buch', 'backlog', '', '2026-01-01', '2026-01-01')"))  # fmt: skip
        v.execute(text("INSERT INTO buecher (id, autoren, verlag) VALUES (1, 'A', 'V')"))
        v.commit()
    sicherung = migrieren(engine, pfad)
    assert sicherung is not None and sicherung.exists()
    with engine.connect() as v:
        assert v.execute(text("SELECT titel, reihe, reihe_nr FROM medien JOIN buecher USING (id)")).one() == (
            "Altes Buch", "", None
        )
        spalten = {z[1] for z in v.execute(text("PRAGMA table_info(spiele)"))}
    assert {"header_url", "erfolge_erreicht", "erfolge_gesamt"} <= spalten
    engine.dispose()


def test_welche_regale_angezeigt_werden(client, sitzung):
    assert re.findall(r'id="regal-(\d+)"', client.get("/buecher").text) == ["1"]  # leer → erstes Regal
    _buecher(sitzung, *[f"B{i}" for i in range(60)])
    assert re.findall(r'id="regal-(\d+)"', client.get("/buecher").text) == ["1", "2"]
    _buecher(sitzung, *[f"C{i}" for i in range(40)])  # 100 → Regal 2 voll → leeres Regal 3 dazu
    assert re.findall(r'id="regal-(\d+)"', client.get("/buecher").text) == ["1", "2", "3"]
