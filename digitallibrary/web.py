"""Weboberfläche: FastAPI + Jinja2 + HTMX."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import bibliothek as bib
from . import isbn as isbn_modul
from . import openlibrary, steam
from .config import Einstellungen
from .db import engine_erstellen, migrieren
from .models import STATUS_REIHENFOLGE, Buch, Medium, Spiel, Status

PAKET = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=PAKET / "templates")
templates.env.globals.update(STATUS_REIHENFOLGE=STATUS_REIHENFOLGE, Status=Status)

TABS: dict[str, type[Medium]] = {"spiele": Spiel, "buecher": Buch}
BUCH_FELDER = (
    "titel", "isbn", "autoren", "verlag", "jahr", "seiten", "cover_url", "openlibrary_key", "reihe", "reihe_nr"
)  # fmt: skip


def stunden(minuten: int | None) -> str:
    """90 → "1,5"; None → ""."""
    if minuten is None:
        return ""
    text = f"{minuten / 60:.1f}".replace(".", ",")
    return text.removesuffix(",0")


templates.env.filters["stunden"] = stunden


# --- Eingaben aus Formularen -------------------------------------------------


def _zahl(text: str, feld: str, minimum: int, maximum: int) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+", text):
        raise bib.Eingabefehler(f"{feld}: bitte eine ganze Zahl eingeben.")
    wert = int(text)
    if not minimum <= wert <= maximum:
        raise bib.Eingabefehler(f"{feld}: bitte eine Zahl zwischen {minimum} und {maximum} eingeben.")
    return wert


def _dauer_min(text: str) -> int | None:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    if not re.fullmatch(r"\d{1,4}(\.\d+)?", text):
        raise bib.Eingabefehler("Geschätzte Dauer: bitte Stunden angeben, z. B. 12 oder 1,5.")
    return round(float(text) * 60)


def _isbn(text: str) -> str | None:
    if not (text or "").strip():
        return None
    isbn13 = isbn_modul.normalisieren(text)
    if isbn13 is None:
        raise bib.Eingabefehler("Die ISBN ist ungültig (Prüfziffer stimmt nicht). Feld leer lassen geht auch.")
    return isbn13


def _cover_url(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    if not text.startswith("https://") or len(text) > 500:
        raise bib.Eingabefehler("Die Cover-URL muss mit https:// beginnen.")
    return text


def _status(text: str) -> Status:
    try:
        return Status(text)
    except ValueError:
        raise bib.Eingabefehler("Unbekannter Status.") from None


def _buch_eingabe(formular: dict[str, str]) -> bib.BuchEingabe:
    return bib.BuchEingabe(
        titel=formular["titel"],
        isbn13=_isbn(formular["isbn"]),
        autoren=formular["autoren"],
        verlag=formular["verlag"],
        jahr=_zahl(formular["jahr"], "Jahr", 1, 2100),
        seiten=_zahl(formular["seiten"], "Seiten", 1, 100_000),
        cover_url=_cover_url(formular["cover_url"]),
        openlibrary_key=formular["openlibrary_key"].strip()[:50] or None,
        reihe=formular.get("reihe", ""),
        reihe_nr=_zahl(formular.get("reihe_nr", ""), "Band", 0, 10_000),
    )


def _formular_aus_daten(daten: openlibrary.BuchDaten) -> dict[str, str]:
    return {
        "titel": daten.titel,
        "isbn": daten.isbn13,
        "autoren": daten.autoren,
        "verlag": daten.verlag,
        "jahr": str(daten.jahr or ""),
        "seiten": str(daten.seiten or ""),
        "cover_url": daten.cover_url or "",
        "openlibrary_key": daten.openlibrary_key or "",
        "reihe": "",
        "reihe_nr": "",
    }


def _leeres_formular(isbn13: str = "") -> dict[str, str]:
    formular = dict.fromkeys(BUCH_FELDER, "")
    formular["isbn"] = isbn13
    return formular


# --- App ------------------------------------------------------------------------


def app_erstellen(
    einstellungen: Einstellungen | None = None,
    engine: Engine | None = None,
    http_client: httpx.Client | None = None,
) -> FastAPI:
    einstellungen = einstellungen or Einstellungen()
    if engine is None:
        pfad = einstellungen.datenbank_pfad()
        engine = engine_erstellen(pfad)
        migrieren(engine, pfad)

    app = FastAPI(title="DigitalLibrary", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sitzungen = sessionmaker(engine, expire_on_commit=False)
    app.state.http = http_client or openlibrary.client_erstellen()
    app.mount("/static", StaticFiles(directory=PAKET / "static"), name="static")

    def sitzung(request: Request) -> Iterator[Session]:
        with request.app.state.sitzungen() as s:
            yield s

    Sitzung = Annotated[Session, Depends(sitzung)]

    def medium_oder_404(s: Session, medium_id: int) -> Medium:
        medium = bib.medium_holen(s, medium_id)
        if medium is None:
            raise HTTPException(404, "Nicht gefunden")
        return medium

    def tab_von(medium: Medium) -> str:
        return "buecher" if isinstance(medium, Buch) else "spiele"

    # --- Listen ---

    @app.get("/")
    def start() -> RedirectResponse:
        return RedirectResponse("/naechstes", status_code=303)

    @app.get("/naechstes", response_class=HTMLResponse)
    def naechstes(request: Request, s: Sitzung, art: str = "", stimmung: str = "") -> HTMLResponse:
        art = art if art in ("spiel", "buch") else ""
        stimmungen = bib.stimmungen_alle(s)
        gewaehlt = next((st for st in stimmungen if str(st.id) == stimmung), None)
        ergebnis = bib.vorschlaege(s, art or None, gewaehlt.id if gewaehlt else None)
        return templates.TemplateResponse(
            request,
            "naechstes.html",
            {
                "tab": "naechstes",
                "art": art,
                "stimmungen": stimmungen,
                "stimmung": gewaehlt,
                "ergebnis": ergebnis,
            },
        )

    @app.post("/naechstes/starten/{medium_id}", response_class=HTMLResponse)
    def vorschlag_starten(request: Request, s: Sitzung, medium_id: int) -> HTMLResponse:
        medium = medium_oder_404(s, medium_id)
        bib.status_setzen(medium, Status.LAEUFT)
        s.commit()
        return templates.TemplateResponse(request, "_gestartet.html", {"m": medium})

    def sortierungen(tab: str) -> dict[str, str]:
        return bib.SORTIERUNGEN if tab == "spiele" else bib.BUCH_SORTIERUNGEN

    def tab_url(tab: str, params: dict[str, str | None]) -> str:
        query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
        return f"/{tab}" + (f"?{query}" if query else "")

    def liste(
        request: Request, s: Session, tab: str, params: dict[str, str], *, meldung: str = "", fehler: str = ""
    ) -> HTMLResponse:
        """Listen- bzw. Regalansicht. params: status, sort, erfolge (nur Spiele), ansicht (nur Bücher)."""
        klasse = TABS[tab]
        filter_status = Status(params["status"]) if params.get("status") in {st.value for st in Status} else None
        regal = tab == "buecher" and params.get("ansicht") != "liste"
        standard_sort = "autor" if regal else "status"
        sortierung = params.get("sort") if params.get("sort") in sortierungen(tab) else standard_sort
        erfolge = params.get("erfolge") if tab == "spiele" and params.get("erfolge") in bib.ERFOLGE_FILTER else None
        aktuell = {
            "ansicht": "liste" if tab == "buecher" and not regal else None,
            "status": None if regal or filter_status is None else filter_status.value,
            "erfolge": erfolge,
            "sort": None if sortierung == standard_sort else sortierung,
        }

        def link(**aenderungen: str | None) -> str:
            return tab_url(tab, {**aktuell, **aenderungen})

        kontext: dict[str, object] = {
            "tab": tab,
            "link": link,
            "aktuell": aktuell,
            "hier": link(),
            "sortierung": sortierung,
            "sortierungen": sortierungen(tab),
            "meldung": meldung,
            "fehler": fehler,
        }
        if tab == "buecher":
            buecher = bib.buecher_sortiert(s, sortierung)
            kontext.update(buecher_anzahl=len(buecher), kapazitaet=bib.KAPAZITAET)
            if regal:
                kontext.update(regale=bib.regale_fuellen(buecher), plaetze_pro_regal=bib.PLAETZE_PRO_REGAL)
                return templates.TemplateResponse(request, "regal.html", kontext)
            medien: list[Medium] = [b for b in buecher if filter_status is None or b.status == filter_status]
        else:
            medien = bib.medien_liste(s, klasse, filter_status, sortierung, erfolge)
            kontext.update(
                erfolge_filter=erfolge,
                erfolge_anzahl=bib.anzahl_erfolge(s, filter_status),
                ERFOLGE_FILTER=bib.ERFOLGE_FILTER,
            )
        anzahl = bib.anzahl_je_status(s, klasse)
        kontext.update(
            medien=medien,
            anzahl=anzahl,
            gesamt=sum(anzahl.values()),
            status_filter=filter_status,
            stimmungen=bib.stimmungen_alle(s),
        )
        return templates.TemplateResponse(request, "liste.html", kontext)

    def listen_params(request: Request) -> dict[str, str]:
        erlaubt = ("status", "sort", "erfolge", "ansicht")
        return {k: request.query_params[k] for k in erlaubt if k in request.query_params}

    def geaendert_meldung(request: Request) -> str:
        for param, text in (("geaendert", "Status"), ("stimmung_geaendert", "Stimmung")):
            anzahl = request.query_params.get(param, "")
            if anzahl.isdigit():
                return "Keine Änderung." if anzahl == "0" else f"{text} bei {anzahl} Einträgen geändert."
        return ""

    def zurueck_mit(zurueck: str, **params: object) -> str:
        """Zurück zur Liste, von der die Mehrfachauswahl kam – nur eigene Listen-URLs erlaubt."""
        teile = urlsplit(zurueck)
        if teile.scheme or teile.netloc or teile.path not in ("/spiele", "/buecher"):
            raise HTTPException(422, "Ungültige Rücksprungadresse")
        query = [(k, v) for k, v in parse_qsl(teile.query) if k not in ("geaendert", "stimmung_geaendert")]
        query += [(k, str(v)) for k, v in params.items()]
        return teile.path + (f"?{urlencode(query)}" if query else "")

    @app.get("/spiele", response_class=HTMLResponse)
    def spiele(request: Request, s: Sitzung) -> HTMLResponse:
        return liste(request, s, "spiele", listen_params(request), meldung=geaendert_meldung(request))

    @app.post("/medien/status")
    def status_mehrere(
        s: Sitzung,
        neuer_status: Annotated[str, Form()],
        zurueck: Annotated[str, Form()],
        ids: Annotated[list[int] | None, Form()] = None,
    ) -> RedirectResponse:
        try:
            status = _status(neuer_status)
        except bib.Eingabefehler as fehler:
            raise HTTPException(422, str(fehler)) from None
        zurueck_mit(zurueck)  # vor der Änderung prüfen
        anzahl = bib.status_mehrere(s, ids or [], status)
        s.commit()
        return RedirectResponse(zurueck_mit(zurueck, geaendert=anzahl), status_code=303)

    @app.post("/medien/stimmung")
    def stimmung_mehrere(
        s: Sitzung,
        stimmung_id: Annotated[int, Form()],
        aktion: Annotated[str, Form()],
        zurueck: Annotated[str, Form()],
        ids: Annotated[list[int] | None, Form()] = None,
    ) -> RedirectResponse:
        if aktion not in ("hinzufuegen", "entfernen"):
            raise HTTPException(422, "Ungültige Anfrage")
        zurueck_mit(zurueck)  # vor der Änderung prüfen
        try:
            anzahl = bib.stimmung_mehrere(s, ids or [], stimmung_id, hinzufuegen=aktion == "hinzufuegen")
        except bib.Eingabefehler as fehler:
            raise HTTPException(422, str(fehler)) from None
        s.commit()
        return RedirectResponse(zurueck_mit(zurueck, stimmung_geaendert=anzahl), status_code=303)

    @app.post("/spiele/steam-import", response_class=HTMLResponse)
    def steam_import(request: Request, s: Sitzung) -> HTMLResponse:
        if not (einstellungen.steam_api_key and einstellungen.steam_id):
            return liste(request, s, "spiele", {}, fehler=(
                "Für den Steam-Import fehlen STEAM_API_KEY und/oder STEAM_ID in der .env. "
                "Eintragen und die App neu starten."
            ))  # fmt: skip
        key = einstellungen.steam_api_key.get_secret_value()
        steam_id, http = einstellungen.steam_id, request.app.state.http
        try:
            gefunden = steam.eigene_spiele(key, steam_id, http)
        except steam.SteamFehler as fehler:
            return liste(request, s, "spiele", {}, fehler=str(fehler))
        appids = [g.appid for g in gefunden]
        # Bilder und Erfolge sind Zugabe: schlagen sie fehl, läuft der Abgleich trotzdem durch
        hinweise = []
        try:
            bilder = steam.store_bilder(appids, http)
        except steam.SteamFehler:
            bilder = None
            hinweise.append("Cover konnten nicht aktualisiert werden.")
        try:
            erfolge = steam.erfolge(key, steam_id, appids, http)
        except steam.SteamFehler:
            erfolge = None
            hinweise.append("Erfolge konnten nicht abgefragt werden.")
        ergebnis = bib.steam_importieren(s, gefunden, bilder, erfolge)
        s.commit()
        meldung = (
            f"Steam abgeglichen: {ergebnis.neu} neu, {ergebnis.aktualisiert} aktualisiert, "
            f"{ergebnis.unveraendert} unverändert."
        )
        return liste(request, s, "spiele", {}, meldung=" ".join([meldung, *hinweise]))

    @app.get("/buecher", response_class=HTMLResponse)
    def buecher(request: Request, s: Sitzung) -> HTMLResponse:
        return liste(request, s, "buecher", listen_params(request), meldung=geaendert_meldung(request))

    # --- Buch hinzufügen ---

    @app.get("/buecher/neu", response_class=HTMLResponse)
    def buch_neu(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "buch_neu.html", {"tab": "buecher", "formular": None})

    @app.get("/buecher/formular", response_class=HTMLResponse)
    def buch_formular_leer(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "_buch_formular.html", {"formular": _leeres_formular()})

    @app.post("/buecher/suche", response_class=HTMLResponse)
    def buch_suche(request: Request, s: Sitzung, isbn: Annotated[str, Form()] = "") -> HTMLResponse:
        def antwort(**kontext) -> HTMLResponse:
            return templates.TemplateResponse(request, "_buch_formular.html", {"formular": None, **kontext})

        isbn13 = isbn_modul.normalisieren(isbn)
        if isbn13 is None:
            return antwort(fehler="Das ist keine gültige ISBN (10 oder 13 Ziffern, Prüfziffer stimmt nicht).")
        if vorhanden := bib.buch_mit_isbn(s, isbn13):
            return antwort(fehler=f"Dieses Buch ist schon in der Bibliothek: „{vorhanden.titel}“.", vorhanden=vorhanden)
        try:
            daten = openlibrary.buch_suchen(isbn13, request.app.state.http)
        except openlibrary.OpenLibraryFehler:
            return antwort(
                hinweis="Die Open Library ist gerade nicht erreichbar – bitte von Hand ausfüllen.",
                formular=_leeres_formular(isbn13),
            )
        if daten is None:
            return antwort(
                hinweis="Die Open Library kennt diese ISBN nicht – bitte von Hand ausfüllen.",
                formular=_leeres_formular(isbn13),
            )
        return antwort(
            hinweis="Gefunden! Daten stammen von der Open Library – bitte kurz prüfen, dann speichern.",
            formular=_formular_aus_daten(daten),
        )

    @app.post("/buecher", response_model=None)
    def buch_speichern(
        request: Request,
        s: Sitzung,
        titel: Annotated[str, Form()] = "",
        isbn: Annotated[str, Form()] = "",
        autoren: Annotated[str, Form()] = "",
        verlag: Annotated[str, Form()] = "",
        jahr: Annotated[str, Form()] = "",
        seiten: Annotated[str, Form()] = "",
        cover_url: Annotated[str, Form()] = "",
        openlibrary_key: Annotated[str, Form()] = "",
        reihe: Annotated[str, Form()] = "",
        reihe_nr: Annotated[str, Form()] = "",
    ) -> Response:
        formular = {
            "titel": titel, "isbn": isbn, "autoren": autoren, "verlag": verlag,
            "jahr": jahr, "seiten": seiten, "cover_url": cover_url, "openlibrary_key": openlibrary_key,
            "reihe": reihe, "reihe_nr": reihe_nr,
        }  # fmt: skip
        try:
            buch = bib.buch_anlegen(s, _buch_eingabe(formular))
            s.commit()
        except bib.Eingabefehler as fehler:
            s.rollback()
            return templates.TemplateResponse(
                request,
                "buch_neu.html",
                {
                    "tab": "buecher",
                    "formular": formular,
                    "fehler": str(fehler),
                    "vorhanden": getattr(fehler, "buch", None),
                },
                status_code=422,
            )
        return RedirectResponse(f"/medien/{buch.id}", status_code=303)

    # --- Medium ansehen / bearbeiten ---

    def detail(request: Request, s: Session, medium: Medium, *, fehler: str = "", status_code: int = 200):
        return templates.TemplateResponse(
            request,
            "medium.html",
            {
                "tab": tab_von(medium),
                "m": medium,
                "stimmungen": bib.stimmungen_alle(s),
                "gespeichert": request.query_params.get("gespeichert") == "1",
                "fehler": fehler,
            },
            status_code=status_code,
        )

    @app.get("/medien/{medium_id}", response_class=HTMLResponse)
    def medium_ansehen(request: Request, s: Sitzung, medium_id: int) -> HTMLResponse:
        return detail(request, s, medium_oder_404(s, medium_id))

    @app.post("/medien/{medium_id}", response_model=None)
    def medium_speichern(
        request: Request,
        s: Sitzung,
        medium_id: int,
        titel: Annotated[str, Form()] = "",
        status: Annotated[str, Form()] = "backlog",
        bewertung: Annotated[str, Form()] = "",
        dauer_h: Annotated[str, Form()] = "",
        notiz: Annotated[str, Form()] = "",
        stimmung_ids: Annotated[list[int] | None, Form()] = None,
        isbn: Annotated[str, Form()] = "",
        autoren: Annotated[str, Form()] = "",
        verlag: Annotated[str, Form()] = "",
        jahr: Annotated[str, Form()] = "",
        seiten: Annotated[str, Form()] = "",
        aktuelle_seite: Annotated[str, Form()] = "",
        cover_url: Annotated[str, Form()] = "",
        reihe: Annotated[str, Form()] = "",
        reihe_nr: Annotated[str, Form()] = "",
    ) -> Response:
        medium = medium_oder_404(s, medium_id)
        try:
            if not titel.strip():
                raise bib.Eingabefehler("Bitte einen Titel angeben.")
            medium.titel = titel.strip()[:300]
            bib.status_setzen(medium, _status(status))
            medium.bewertung = bib.bewertung_pruefen(_zahl(bewertung, "Bewertung", 1, 10))
            medium.dauer_min = _dauer_min(dauer_h)
            medium.notiz = notiz.strip()[:5000]
            medium.cover_url = _cover_url(cover_url)
            bib.stimmungen_setzen(s, medium, stimmung_ids or [])
            if isinstance(medium, Buch):
                neue_isbn = _isbn(isbn)
                if neue_isbn and neue_isbn != medium.isbn13 and (andere := bib.buch_mit_isbn(s, neue_isbn)):
                    raise bib.BuchSchonVorhanden(andere)
                medium.isbn13 = neue_isbn
                medium.autoren = autoren.strip()[:500]
                medium.verlag = verlag.strip()[:200]
                medium.jahr = _zahl(jahr, "Jahr", 1, 2100)
                medium.seiten = _zahl(seiten, "Seiten", 1, 100_000)
                medium.reihe = " ".join(reihe.split())[:200]
                medium.reihe_nr = _zahl(reihe_nr, "Band", 0, 10_000)
                bib.aktuelle_seite_setzen(medium, _zahl(aktuelle_seite, "Aktuelle Seite", 0, 100_000))
            s.commit()
        except bib.Eingabefehler as fehler:
            s.rollback()
            return detail(request, s, medium_oder_404(s, medium_id), fehler=str(fehler), status_code=422)
        return RedirectResponse(f"/medien/{medium_id}?gespeichert=1", status_code=303)

    @app.post("/medien/{medium_id}/status", response_class=HTMLResponse)
    def medium_status(request: Request, s: Sitzung, medium_id: int, status: Annotated[str, Form()]) -> HTMLResponse:
        medium = medium_oder_404(s, medium_id)
        try:
            bib.status_setzen(medium, _status(status))
        except bib.Eingabefehler as fehler:
            raise HTTPException(422, str(fehler)) from None
        s.commit()
        return templates.TemplateResponse(request, "_karte.html", {"m": medium})

    @app.post("/medien/{medium_id}/loeschen")
    def medium_loeschen(s: Sitzung, medium_id: int) -> RedirectResponse:
        medium = medium_oder_404(s, medium_id)
        tab = tab_von(medium)
        s.delete(medium)
        s.commit()
        return RedirectResponse(f"/{tab}", status_code=303)

    @app.post("/stimmungen", response_model=None)
    def stimmung_neu(
        request: Request, s: Sitzung, name: Annotated[str, Form()] = "", zurueck: Annotated[int, Form()] = 0
    ) -> Response:
        medium = medium_oder_404(s, zurueck)
        try:
            bib.stimmung_anlegen(s, name)
            s.commit()
        except bib.Eingabefehler as fehler:
            s.rollback()
            return detail(request, s, medium, fehler=str(fehler), status_code=422)
        return RedirectResponse(f"/medien/{medium.id}#stimmungen", status_code=303)

    return app
