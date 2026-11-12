"""Fachlogik: Medien anlegen, ändern, auflisten. Unabhängig von der Weboberfläche."""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from . import steam
from .models import STATUS_REIHENFOLGE, Buch, Fortschritt, Medium, Spiel, Status, Stimmung
from .steam import SteamSpiel


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


SORTIERUNGEN = {"status": "Status", "titel": "Titel", "spielzeit": "Spielzeit", "zuletzt": "Zuletzt gespielt"}


def medien_liste(
    sitzung: Session, klasse: type[Medium], status: Status | None = None, sortierung: str = "status"
) -> list[Medium]:
    abfrage = select(klasse)
    if status is not None:
        abfrage = abfrage.where(klasse.status == status)
    titel = func.lower(klasse.titel)
    if sortierung == "titel":
        abfrage = abfrage.order_by(titel)
    elif sortierung == "spielzeit" and issubclass(klasse, Spiel):
        abfrage = abfrage.order_by(Spiel.spielzeit_min.desc(), titel)
    elif sortierung == "zuletzt" and issubclass(klasse, Spiel):
        abfrage = abfrage.order_by(Spiel.zuletzt_gespielt.desc().nulls_last(), titel)
    else:
        abfrage = abfrage.order_by(_status_sortierung(), titel)
    return list(sitzung.scalars(abfrage))


def status_mehrere(sitzung: Session, ids: list[int], status: Status, heute: dt.date | None = None) -> int:
    """Setzt den Status für mehrere Medien auf einmal. Gibt die Anzahl der geänderten zurück."""
    if not ids:
        return 0
    geaendert = 0
    for medium in sitzung.scalars(select(Medium).where(Medium.id.in_(ids))):
        if medium.status != status:
            status_setzen(medium, status, heute)
            geaendert += 1
    sitzung.flush()
    return geaendert


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


# --- Steam -------------------------------------------------------------------


@dataclass
class ImportErgebnis:
    neu: int = 0
    aktualisiert: int = 0
    unveraendert: int = 0


def steam_importieren(sitzung: Session, spiele: list[SteamSpiel]) -> ImportErgebnis:
    """Gleicht die Steam-Bibliothek ab.

    Neue Spiele kommen in den Backlog (in den letzten 2 Wochen gespielt → „Läuft“).
    Bei vorhandenen werden nur Spielzeit und „zuletzt gespielt“ aktualisiert – Titel, Status,
    Bewertung usw. gehören Rico und bleiben unangetastet. Jede Spielzeit-Änderung wird als
    Momentaufnahme in `fortschritt` gespeichert. Spiele, die nicht mehr bei Steam sind, bleiben.
    """
    vorhandene = {s.steam_appid: s for s in sitzung.scalars(select(Spiel).where(Spiel.steam_appid.is_not(None)))}
    ergebnis = ImportErgebnis()
    for daten in spiele:
        spiel = vorhandene.get(daten.appid)
        if spiel is None:
            spiel = Spiel(
                titel=daten.name,
                steam_appid=daten.appid,
                spielzeit_min=daten.spielzeit_min,
                zuletzt_gespielt=daten.zuletzt_gespielt,
                cover_url=steam.cover_url(daten.appid),
                status=Status.LAEUFT if daten.spielzeit_2wochen_min else Status.BACKLOG,
            )
            if spiel.status == Status.LAEUFT:
                spiel.begonnen_am = dt.date.today()
            if daten.spielzeit_min:
                spiel.fortschritt.append(Fortschritt(wert=daten.spielzeit_min))
            sitzung.add(spiel)
            vorhandene[daten.appid] = spiel
            ergebnis.neu += 1
        elif spiel.spielzeit_min != daten.spielzeit_min or spiel.zuletzt_gespielt != daten.zuletzt_gespielt:
            if spiel.spielzeit_min != daten.spielzeit_min:
                spiel.fortschritt.append(Fortschritt(wert=daten.spielzeit_min))
            spiel.spielzeit_min = daten.spielzeit_min
            spiel.zuletzt_gespielt = daten.zuletzt_gespielt
            ergebnis.aktualisiert += 1
        else:
            ergebnis.unveraendert += 1
    sitzung.flush()
    return ergebnis
