"""Fachlogik: Medien anlegen, ändern, auflisten. Unabhängig von der Weboberfläche."""

import datetime as dt
import random
import unicodedata
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

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
    reihe: str = ""
    reihe_nr: int | None = None


def _status_sortierung():
    return case({s.value: i for i, s in enumerate(STATUS_REIHENFOLGE)}, value=Medium.status)


SORTIERUNGEN = {
    "status": "Status",
    "titel": "Titel",
    "spielzeit": "Spielzeit",
    "zuletzt": "Zuletzt gespielt",
    "erfolge": "Erfolge (%)",
}

ERFOLGE_FILTER = {
    "komplett": "100 %",
    "angefangen": "Angefangen",
    "keine": "Noch keiner",
    "ohne": "Ohne Erfolge",
}


def _erfolge_bedingung(art: str):
    erreicht = func.coalesce(Spiel.erfolge_erreicht, 0)
    return {
        "komplett": (Spiel.erfolge_gesamt > 0) & (erreicht >= Spiel.erfolge_gesamt),
        "angefangen": (erreicht > 0) & (erreicht < Spiel.erfolge_gesamt),
        "keine": (Spiel.erfolge_gesamt > 0) & (erreicht == 0),
        "ohne": Spiel.erfolge_gesamt == 0,
    }[art]


def medien_liste(
    sitzung: Session,
    klasse: type[Medium],
    status: Status | None = None,
    sortierung: str = "status",
    erfolge: str | None = None,
) -> list[Medium]:
    # Stimmungen gesammelt in einer Abfrage laden statt einzeln pro Karte
    abfrage = select(klasse).options(selectinload(klasse.stimmungen))
    if status is not None:
        abfrage = abfrage.where(klasse.status == status)
    ist_spiel = issubclass(klasse, Spiel)
    if erfolge in ERFOLGE_FILTER and ist_spiel:
        abfrage = abfrage.where(_erfolge_bedingung(erfolge))
    titel = func.lower(klasse.titel)
    if sortierung == "titel":
        abfrage = abfrage.order_by(titel)
    elif sortierung == "spielzeit" and ist_spiel:
        abfrage = abfrage.order_by(Spiel.spielzeit_min.desc(), titel)
    elif sortierung == "zuletzt" and ist_spiel:
        abfrage = abfrage.order_by(Spiel.zuletzt_gespielt.desc().nulls_last(), titel)
    elif sortierung == "erfolge" and ist_spiel:
        prozent = case(
            (Spiel.erfolge_gesamt > 0, func.coalesce(Spiel.erfolge_erreicht, 0) * 1.0 / Spiel.erfolge_gesamt)
        )
        abfrage = abfrage.order_by(prozent.desc().nulls_last(), Spiel.erfolge_gesamt.desc(), titel)
    else:
        abfrage = abfrage.order_by(_status_sortierung(), titel)
    return list(sitzung.scalars(abfrage))


def anzahl_erfolge(sitzung: Session, status: Status | None = None) -> dict[str, int]:
    anzahl = {}
    for art in ERFOLGE_FILTER:
        abfrage = select(func.count()).select_from(Spiel).where(_erfolge_bedingung(art))
        if status is not None:
            abfrage = abfrage.where(Spiel.status == status)
        anzahl[art] = sitzung.scalar(abfrage) or 0
    return anzahl


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
        reihe=" ".join(eingabe.reihe.split())[:200],
        reihe_nr=eingabe.reihe_nr,
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


def stimmung_mehrere(sitzung: Session, ids: list[int], stimmung_id: int, hinzufuegen: bool) -> int:
    """Fügt eine Stimmung mehreren Medien hinzu bzw. entfernt sie. Gibt die Anzahl der geänderten zurück."""
    stimmung = sitzung.get(Stimmung, stimmung_id)
    if stimmung is None:
        raise Eingabefehler("Diese Stimmung gibt es nicht.")
    if not ids:
        return 0
    geaendert = 0
    for medium in sitzung.scalars(select(Medium).where(Medium.id.in_(ids))):
        hat = stimmung in medium.stimmungen
        if hinzufuegen and not hat:
            medium.stimmungen.append(stimmung)
            geaendert += 1
        elif not hinzufuegen and hat:
            medium.stimmungen.remove(stimmung)
            geaendert += 1
    sitzung.flush()
    return geaendert


# --- Bücherregal ----------------------------------------------------------------

PLAETZE_PRO_BODEN = 10
BOEDEN_PRO_REGAL = 5
PLAETZE_PRO_REGAL = PLAETZE_PRO_BODEN * BOEDEN_PRO_REGAL  # 50
ANZAHL_REGALE = 20
KAPAZITAET = PLAETZE_PRO_REGAL * ANZAHL_REGALE  # 1000 → offizielle Bibliothek

BUCH_SORTIERUNGEN = {
    "autor": "Autor",
    "reihe": "Reihe",
    "titel": "Titel",
    "jahr": "Erscheinungsjahr",
    "neu": "Zuletzt hinzugefügt",
    "status": "Status",
}


def _sortiertext(text: str) -> str:
    """Für Sortierung: ohne Groß/klein und Akzente (Ö → o, é → e)."""
    zerlegt = unicodedata.normalize("NFKD", text.casefold())
    return "".join(z for z in zerlegt if not unicodedata.combining(z)).strip()


def nachname(autoren: str) -> str:
    """Nachname des ersten Autors: „Frank Herbert, Brian Herbert“ → „herbert“."""
    erster = autoren.split(",")[0].strip()
    return _sortiertext(erster.split()[-1]) if erster else ""


def buecher_sortiert(sitzung: Session, sortierung: str = "autor") -> list[Buch]:
    buecher = list(sitzung.scalars(select(Buch).options(selectinload(Buch.stimmungen))))
    titel = lambda b: _sortiertext(b.titel)  # noqa: E731
    ohne = (1,)  # fehlende Angaben ans Ende
    schluessel = {
        "autor": lambda b: ((0, nachname(b.autoren), _sortiertext(b.autoren)) if b.autoren.strip() else ohne,
                            _sortiertext(b.reihe), b.reihe_nr or 0, titel(b)),
        "reihe": lambda b: ((0, _sortiertext(b.reihe), b.reihe_nr if b.reihe_nr is not None else 10**6)
                            if b.reihe.strip() else ohne, titel(b)),
        "titel": lambda b: (titel(b),),
        "jahr": lambda b: ((0, b.jahr) if b.jahr else ohne, titel(b)),
        "neu": lambda b: (-b.hinzugefuegt_am.timestamp(), titel(b)),
        "status": lambda b: (STATUS_REIHENFOLGE.index(b.status), titel(b)),
    }.get(sortierung)  # fmt: skip
    return sorted(buecher, key=schluessel or (lambda b: (titel(b),)))


@dataclass
class Regal:
    nr: int
    boeden: list[list[Buch]]

    @property
    def anzahl(self) -> int:
        return sum(len(b) for b in self.boeden)

    @property
    def fuellstand(self) -> float:
        return self.anzahl / PLAETZE_PRO_REGAL


def regale_fuellen(buecher: list[Buch], mindestens: int = ANZAHL_REGALE) -> list[Regal]:
    """Verteilt die Bücher der Reihe nach auf Regale (50 Plätze, 5 Böden à 10).

    Es gibt immer mindestens 20 Regale (= 1000 Plätze). Werden es mehr Bücher, kommen Anbauten dazu.
    """
    anzahl_regale = max(mindestens, -(-len(buecher) // PLAETZE_PRO_REGAL))
    regale = []
    for nr in range(anzahl_regale):
        inhalt = buecher[nr * PLAETZE_PRO_REGAL : (nr + 1) * PLAETZE_PRO_REGAL]
        boeden = [inhalt[i * PLAETZE_PRO_BODEN : (i + 1) * PLAETZE_PRO_BODEN] for i in range(BOEDEN_PRO_REGAL)]
        regale.append(Regal(nr=nr + 1, boeden=boeden))
    return regale


# --- Was als Nächstes? -----------------------------------------------------------


@dataclass
class Vorschlaege:
    weitermachen: list[Medium]
    neu: list[Medium]
    kandidaten: int  # so viele Backlog-Einträge passten zur Auswahl


def gewicht(medium: Medium, jetzt: dt.datetime) -> float:
    """Länger Wartendes kommt häufiger dran: 1 + 1 pro 30 Tage im Backlog, höchstens 13."""
    tage = max((jetzt - medium.hinzugefuegt_am).days, 0)
    return 1 + min(tage, 360) / 30


def vorschlaege(
    sitzung: Session,
    art: str | None = None,
    stimmung_id: int | None = None,
    anzahl: int = 3,
    zufall: random.Random | None = None,
    jetzt: dt.datetime | None = None,
) -> Vorschlaege:
    zufall = zufall or random.Random()  # noqa: S311 - kein Krypto-Zweck
    jetzt = jetzt or dt.datetime.now()

    def abfrage(status: Status):
        q = select(Medium).options(selectinload(Medium.stimmungen)).where(Medium.status == status)
        if art:
            q = q.where(Medium.art == art)
        if stimmung_id is not None:
            q = q.where(Medium.stimmungen.any(Stimmung.id == stimmung_id))
        return q

    weitermachen = list(
        sitzung.scalars(abfrage(Status.LAEUFT).order_by(Medium.geaendert_am.desc(), func.lower(Medium.titel)))
    )
    # Feste Reihenfolge vor dem Ziehen, damit Tests mit festem Startwert reproduzierbar sind
    topf = list(sitzung.scalars(abfrage(Status.BACKLOG).order_by(Medium.id)))
    kandidaten = len(topf)
    gezogen: list[Medium] = []
    while topf and len(gezogen) < anzahl:
        wahl = zufall.choices(range(len(topf)), weights=[gewicht(m, jetzt) for m in topf])[0]
        gezogen.append(topf.pop(wahl))
    return Vorschlaege(weitermachen=weitermachen, neu=gezogen, kandidaten=kandidaten)


# --- Steam -------------------------------------------------------------------


@dataclass
class ImportErgebnis:
    neu: int = 0
    aktualisiert: int = 0
    unveraendert: int = 0


def _von_steam(url: str | None) -> bool:
    """Cover, die der Abgleich selbst gesetzt hat, darf er auch ersetzen – eigene von Rico nicht."""
    return url is None or url.startswith(steam.ASSETS)


def steam_importieren(
    sitzung: Session,
    spiele: list[SteamSpiel],
    bilder: dict[int, steam.StoreBilder] | None = None,
    erfolge: dict[int, steam.Erfolge] | None = None,
) -> ImportErgebnis:
    """Gleicht die Steam-Bibliothek ab.

    Neue Spiele kommen in den Backlog (in den letzten 2 Wochen gespielt → „Läuft“).
    Bei vorhandenen werden nur Spielzeit, „zuletzt gespielt“, Erfolge und Steam-Bilder
    aktualisiert – Titel, Status, Bewertung, eigene Cover usw. gehören Rico. Jede
    Spielzeit-Änderung wird als Momentaufnahme in `fortschritt` gespeichert.
    Spiele, die nicht mehr bei Steam sind, bleiben.

    bilder/erfolge: None = nicht abgefragt (z. B. Store nicht erreichbar) → vorhandene Werte bleiben.
    """
    vorhandene = {s.steam_appid: s for s in sitzung.scalars(select(Spiel).where(Spiel.steam_appid.is_not(None)))}
    ergebnis = ImportErgebnis()
    for daten in spiele:
        spiel = vorhandene.get(daten.appid)
        neu = spiel is None
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

        vorher = (spiel.spielzeit_min, spiel.zuletzt_gespielt, spiel.erfolge_erreicht, spiel.erfolge_gesamt,
                  spiel.cover_url, spiel.header_url)  # fmt: skip
        if not neu and spiel.spielzeit_min != daten.spielzeit_min:
            spiel.fortschritt.append(Fortschritt(wert=daten.spielzeit_min))
        spiel.spielzeit_min = daten.spielzeit_min
        spiel.zuletzt_gespielt = daten.zuletzt_gespielt
        if bilder is not None and _von_steam(spiel.cover_url):
            gefunden = bilder.get(daten.appid)
            # Ohne Store-Seite (Test-/Beta-Versionen) gibt es kein Bild → gezeichnetes Ersatz-Cover
            spiel.cover_url = gefunden.cover_url if gefunden else None
            spiel.header_url = gefunden.header_url if gefunden else None
        if erfolge is not None and daten.appid in erfolge:
            spiel.erfolge_erreicht = erfolge[daten.appid].erreicht
            spiel.erfolge_gesamt = erfolge[daten.appid].gesamt
        nachher = (spiel.spielzeit_min, spiel.zuletzt_gespielt, spiel.erfolge_erreicht, spiel.erfolge_gesamt,
                   spiel.cover_url, spiel.header_url)  # fmt: skip

        if neu:
            ergebnis.neu += 1
        elif vorher != nachher:
            ergebnis.aktualisiert += 1
        else:
            ergebnis.unveraendert += 1
    sitzung.flush()
    return ergebnis
