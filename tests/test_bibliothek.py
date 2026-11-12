import datetime as dt
import sqlite3

import pytest
from sqlalchemy import select

from digitallibrary import bibliothek as bib
from digitallibrary.db import engine_erstellen, migrieren
from digitallibrary.models import Buch, Fortschritt, Medium, Spiel, Status, Stimmung

HEUTE = dt.date(2026, 11, 12)


def test_migration_legt_standard_stimmungen_an(sitzung):
    assert [s.name for s in bib.stimmungen_alle(sitzung)] == ["entspannt", "fordernd", "Story"]


def test_migration_zweimal_ist_harmlos(engine, db_pfad):
    assert migrieren(engine, db_pfad) is None  # schon aktuell → keine Sicherung, nichts passiert


def test_sicherung_wird_vor_der_migration_angelegt(tmp_path):
    pfad = tmp_path / "alt.db"
    engine = engine_erstellen(pfad)
    migrieren(engine, pfad)
    with engine.begin() as v:
        v.exec_driver_sql("INSERT INTO stimmungen (name) VALUES ('meine')")
        # Abweichender Stand → Migration nötig (scheitert hier, weil es die Revision nicht gibt)
        v.exec_driver_sql("UPDATE alembic_version SET version_num = 'unbekannt'")
    with pytest.raises(Exception):  # noqa: B017 - Alembic-Fehlertyp ist egal
        migrieren(engine, pfad)
    engine.dispose()
    sicherungen = list((tmp_path / "sicherung").glob("alt_*.db"))
    assert len(sicherungen) == 1
    with sqlite3.connect(sicherungen[0]) as kopie:
        namen = [z[0] for z in kopie.execute("SELECT name FROM stimmungen")]
    kopie.close()
    assert "meine" in namen


def test_neue_db_wird_nicht_gesichert(tmp_path):
    pfad = tmp_path / "neu.db"
    engine = engine_erstellen(pfad)
    assert migrieren(engine, pfad) is None
    engine.dispose()
    assert not (tmp_path / "sicherung").exists()


def test_buch_anlegen_und_doppelte_isbn(sitzung):
    buch = bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="  Dune  ", isbn13="9783453317178", seiten=800))
    sitzung.commit()
    assert buch.titel == "Dune"
    assert buch.status == Status.BACKLOG
    assert buch.art == "buch"
    assert buch.geschaetzte_dauer_min == 1200  # 800 Seiten × 1,5 min
    with pytest.raises(bib.BuchSchonVorhanden) as info:
        bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="Nochmal", isbn13="9783453317178"))
    assert info.value.buch.id == buch.id


def test_buecher_ohne_isbn_beliebig_oft(sitzung):
    bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="Altes Buch"))
    bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="Noch ein altes Buch"))
    sitzung.commit()
    assert len(bib.medien_liste(sitzung, Buch)) == 2


def test_titel_pflicht(sitzung):
    with pytest.raises(bib.Eingabefehler):
        bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="   "))


def test_manuelle_dauer_hat_vorrang(sitzung):
    buch = bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="X", seiten=100))
    buch.dauer_min = 60
    assert buch.geschaetzte_dauer_min == 60


def test_status_pflegt_datum():
    buch = Buch(titel="X", status=Status.BACKLOG)
    bib.status_setzen(buch, Status.LAEUFT, heute=HEUTE)
    assert (buch.begonnen_am, buch.beendet_am) == (HEUTE, None)
    spaeter = HEUTE + dt.timedelta(days=10)
    bib.status_setzen(buch, Status.BEENDET, heute=spaeter)
    assert (buch.begonnen_am, buch.beendet_am) == (HEUTE, spaeter)
    bib.status_setzen(buch, Status.LAEUFT, heute=spaeter)  # nochmal angefangen
    assert (buch.begonnen_am, buch.beendet_am) == (HEUTE, None)


def test_direkt_abgebrochen_setzt_beide_daten():
    buch = Buch(titel="X", status=Status.BACKLOG)
    bib.status_setzen(buch, Status.ABGEBROCHEN, heute=HEUTE)
    assert (buch.begonnen_am, buch.beendet_am) == (HEUTE, HEUTE)


def test_liste_sortiert_laufende_zuerst_und_filtert(sitzung):
    for titel, status in [("b", Status.BACKLOG), ("A", Status.BEENDET), ("c", Status.LAEUFT), ("a", Status.BACKLOG)]:
        buch = bib.buch_anlegen(sitzung, bib.BuchEingabe(titel=titel))
        buch.status = status
    sitzung.add(Spiel(titel="Spiel", status=Status.LAEUFT))
    sitzung.commit()
    assert [m.titel for m in bib.medien_liste(sitzung, Buch)] == ["c", "a", "b", "A"]
    assert [m.titel for m in bib.medien_liste(sitzung, Buch, Status.BACKLOG)] == ["a", "b"]
    assert [m.titel for m in bib.medien_liste(sitzung, Spiel)] == ["Spiel"]
    assert len(bib.medien_liste(sitzung, Medium)) == 5
    assert bib.anzahl_je_status(sitzung, Buch) == {
        Status.LAEUFT: 1, Status.BACKLOG: 2, Status.BEENDET: 1, Status.ABGEBROCHEN: 0
    }


def test_aktuelle_seite_schreibt_fortschritt(sitzung):
    buch = bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="X", seiten=300))
    bib.aktuelle_seite_setzen(buch, 50)
    bib.aktuelle_seite_setzen(buch, 50)  # unverändert → kein neuer Eintrag
    bib.aktuelle_seite_setzen(buch, 120)
    sitzung.commit()
    assert [f.wert for f in buch.fortschritt] == [50, 120]
    with pytest.raises(bib.Eingabefehler):
        bib.aktuelle_seite_setzen(buch, 301)


def test_stimmungen_setzen_und_anlegen(sitzung):
    buch = bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="X"))
    neu = bib.stimmung_anlegen(sitzung, "  kurz   weilig ")
    assert neu.name == "kurz weilig"
    assert bib.stimmung_anlegen(sitzung, "STORY").name == "Story"  # vorhandene wird wiederverwendet
    ids = [s.id for s in bib.stimmungen_alle(sitzung) if s.name in ("Story", "entspannt")]
    bib.stimmungen_setzen(sitzung, buch, ids)
    sitzung.commit()
    assert [s.name for s in buch.stimmungen] == ["entspannt", "Story"]
    with pytest.raises(bib.Eingabefehler):
        bib.stimmung_anlegen(sitzung, "   ")


def test_loeschen_raeumt_alles_auf(sitzung):
    buch = bib.buch_anlegen(sitzung, bib.BuchEingabe(titel="X", seiten=10))
    bib.aktuelle_seite_setzen(buch, 5)
    bib.stimmungen_setzen(sitzung, buch, [s.id for s in bib.stimmungen_alle(sitzung)])
    sitzung.commit()
    sitzung.delete(buch)
    sitzung.commit()
    assert sitzung.scalars(select(Medium)).all() == []
    assert sitzung.scalars(select(Fortschritt)).all() == []
    assert len(sitzung.scalars(select(Stimmung)).all()) == 3  # Stimmungen selbst bleiben


def test_bewertung_ausserhalb_bereich_scheitert_in_db(engine, db_pfad):
    with sqlite3.connect(db_pfad) as roh, pytest.raises(sqlite3.IntegrityError):
        roh.execute("INSERT INTO medien (art, titel, status, notiz, hinzugefuegt_am, geaendert_am, bewertung) "
                    "VALUES ('buch', 'x', 'backlog', '', '2026-01-01', '2026-01-01', 11)")
    roh.close()
