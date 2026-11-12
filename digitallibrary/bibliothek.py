"""Fachlogik: Medien anlegen, ändern, auflisten. Unabhängig von der Weboberfläche."""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from .models import STATUS_REIHENFOLGE, Buch, Fortschritt, Medium, Status, Stimmung


class Eingabefehler(ValueError):
    """Ungültige Eingabe – die Meldung ist für Rico gedacht."""


class BuchSchonVorhanden(Eingabefehler):
    def __init__(self, buch: Buch):
        super().__init__(f"Dieses Buch ist schon in der Bibliothek: „{buch.titel}“.")
        self.buch = buch


@dataclass
class BuchEingabe:
    titel: str
    isbn13: str | None = None
    autoren: str = ""
    verlag: str = ""
    jahr: int | None = None
    seiten: int | None = None
    cover_url: str | None = None
    openlibrary_key: str | None = None


def _status_sortierung():
    return case({s.value: i for i, s in enumerate(STATUS_REIHENFOLGE)}, value=Medium.status)


def medien_liste(sitzung: Session, klasse: type[Medium], status: Status | None = None) -> list[Medium]:
    abfrage = select(klasse)
    if status is not None:
        abfrage = abfrage.where(klasse.status == status)
    abfrage = abfrage.order_by(_status_sortierung(), func.lower(klasse.titel))
    return list(sitzung.scalars(abfrage))


def anzahl_je_status(sitzung: Session, klasse: type[Medium]) -> dict[Status, int]:
    zeilen = sitzung.execute(select(klasse.status, func.count()).group_by(klasse.status))
    anzahl = dict.fromkeys(STATUS_REIHENFOLGE, 0)
    anzahl.update({Status(s): n for s, n in zeilen})
    return anzahl


def buch_mit_isbn(sitzung: Session, isbn13: str) -> Buch | None:
    return sitzung.scalar(select(Buch).where(Buch.isbn13 == isbn13))


def buch_anlegen(sitzung: Session, eingabe: BuchEingabe) -> Buch:
    titel = eingabe.titel.strip()
    if not titel:
        raise Eingabefehler("Bitte einen Titel angeben.")
    if eingabe.isbn13 and (vorhanden := buch_mit_isbn(sitzung, eingabe.isbn13)):
        raise BuchSchonVorhanden(vorhanden)
    buch = Buch(
        titel=titel[:300],
        isbn13=eingabe.isbn13 or None,
        autoren=eingabe.autoren.strip()[:500],
        verlag=eingabe.verlag.strip()[:200],
        jahr=eingabe.jahr,
        seiten=eingabe.seiten,
        cover_url=eingabe.cover_url or None,
        openlibrary_key=eingabe.openlibrary_key or None,
    )
    sitzung.add(buch)
    sitzung.flush()
    return buch


def status_setzen(medium: Medium, status: Status, heute: dt.date | None = None) -> None:
    """Setzt den Status und pflegt Beginn-/Enddatum mit (bereits gesetzte Daten bleiben erhalten)."""
    heute = heute or dt.date.today()
    medium.status = status
    if status in (Status.LAEUFT, Status.BEENDET, Status.ABGEBROCHEN) and medium.begonnen_am is None:
        medium.begonnen_am = heute
    if status in (Status.BEENDET, Status.ABGEBROCHEN):
        if medium.beendet_am is None:
            medium.beendet_am = heute
    else:
        medium.beendet_am = None


def aktuelle_seite_setzen(buch: Buch, seite: int | None) -> None:
    """Merkt sich jede Änderung als Momentaufnahme in `fortschritt` (für Verlauf/Statistik)."""
    if seite is not None and buch.seiten and seite > buch.seiten:
        raise Eingabefehler(f"Das Buch hat nur {buch.seiten} Seiten.")
    if seite == buch.aktuelle_seite:
        return
    buch.aktuelle_seite = seite
    if seite is not None:
        buch.fortschritt.append(Fortschritt(wert=seite))


def bewertung_pruefen(bewertung: int | None) -> int | None:
    if bewertung is not None and not 1 <= bewertung <= 10:
        raise Eingabefehler("Die Bewertung muss zwischen 1 und 10 liegen.")
    return bewertung


def stimmungen_alle(sitzung: Session) -> list[Stimmung]:
    return list(sitzung.scalars(select(Stimmung).order_by(func.lower(Stimmung.name))))


def stimmungen_setzen(sitzung: Session, medium: Medium, ids: list[int]) -> None:
    gewaehlt = list(sitzung.scalars(select(Stimmung).where(Stimmung.id.in_(ids)))) if ids else []
    medium.stimmungen = sorted(gewaehlt, key=lambda s: s.name.lower())


def stimmung_anlegen(sitzung: Session, name: str) -> Stimmung:
    name = " ".join(name.split())
    if not name:
        raise Eingabefehler("Bitte einen Namen für die Stimmung angeben.")
    if len(name) > 50:
        raise Eingabefehler("Der Name der Stimmung ist zu lang (max. 50 Zeichen).")
    vorhanden = sitzung.scalar(select(Stimmung).where(func.lower(Stimmung.name) == name.lower()))
    if vorhanden:
        return vorhanden
    stimmung = Stimmung(name=name)
    sitzung.add(stimmung)
    sitzung.flush()
    return stimmung


def medium_holen(sitzung: Session, medium_id: int) -> Medium | None:
    return sitzung.get(Medium, medium_id)
