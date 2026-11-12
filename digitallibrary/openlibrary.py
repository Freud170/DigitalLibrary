"""Buchdaten per ISBN von der Open Library (https://openlibrary.org/dev/docs/api/books).

Die Daten dort sind von allen bearbeitbar (auch Spam kommt vor). Sie werden deshalb nur als
Vorschlag ins Formular übernommen – gespeichert wird erst, wenn Rico bestätigt.
"""

import re
from dataclasses import dataclass

import httpx

URL = "https://openlibrary.org/api/books"
COVER_PRAEFIX = "https://covers.openlibrary.org/"
USER_AGENT = "DigitalLibrary/0.1 (privates, nicht-kommerzielles Projekt)"


class OpenLibraryFehler(Exception):
    """Open Library nicht erreichbar oder Antwort unbrauchbar."""


@dataclass
class BuchDaten:
    isbn13: str
    titel: str
    autoren: str = ""
    verlag: str = ""
    jahr: int | None = None
    seiten: int | None = None
    cover_url: str | None = None
    openlibrary_key: str | None = None


def client_erstellen() -> httpx.Client:
    return httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def _namen(eintraege: object) -> list[str]:
    if not isinstance(eintraege, list):
        return []
    return [str(e["name"]).strip() for e in eintraege if isinstance(e, dict) and e.get("name")]


def _jahr(text: object) -> int | None:
    treffer = re.findall(r"\b(1[5-9]\d{2}|20\d{2})\b", str(text or ""))
    return int(treffer[-1]) if treffer else None


def _cover(eintrag: dict) -> str | None:
    cover = eintrag.get("cover")
    if not isinstance(cover, dict):
        return None
    url = cover.get("medium") or cover.get("large")
    return url if isinstance(url, str) and url.startswith(COVER_PRAEFIX) else None


def daten_auslesen(antwort: dict, isbn13: str) -> BuchDaten | None:
    eintrag = antwort.get(f"ISBN:{isbn13}")
    if not isinstance(eintrag, dict) or not str(eintrag.get("title") or "").strip():
        return None
    titel = str(eintrag["title"]).strip()
    untertitel = str(eintrag.get("subtitle") or "").strip()
    if untertitel and untertitel.lower() != titel.lower():
        titel = f"{titel}: {untertitel}"
    seiten = eintrag.get("number_of_pages")
    key = eintrag.get("key")
    return BuchDaten(
        isbn13=isbn13,
        titel=titel[:300],
        autoren=", ".join(dict.fromkeys(_namen(eintrag.get("authors"))))[:500],
        verlag=", ".join(_namen(eintrag.get("publishers")))[:200],
        jahr=_jahr(eintrag.get("publish_date")),
        seiten=seiten if isinstance(seiten, int) and 0 < seiten < 100_000 else None,
        cover_url=_cover(eintrag),
        openlibrary_key=key[:50] if isinstance(key, str) else None,
    )


def buch_suchen(isbn13: str, client: httpx.Client) -> BuchDaten | None:
    """None = Open Library kennt die ISBN nicht. OpenLibraryFehler = Anfrage fehlgeschlagen."""
    try:
        antwort = client.get(URL, params={"bibkeys": f"ISBN:{isbn13}", "format": "json", "jscmd": "data"})
        antwort.raise_for_status()
        daten = antwort.json()
    except (httpx.HTTPError, ValueError) as fehler:
        raise OpenLibraryFehler(str(fehler)) from fehler
    if not isinstance(daten, dict):
        raise OpenLibraryFehler("Unerwartete Antwort der Open Library")
    return daten_auslesen(daten, isbn13)
