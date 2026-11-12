"""Steam-Bibliothek über die Steam Web API (IPlayerService/GetOwnedGames).

Der API-Key steht nur in der .env und wird nie ausgegeben oder geloggt.
"""

import datetime as dt
import json
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
URL_STORE = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
URL_ERFOLGE = "https://api.steampowered.com/IPlayerService/GetTopAchievementsForGames/v1/"
ASSETS = "https://shared.akamai.steamstatic.com/store_item_assets/"
CDN = ASSETS + "steam/apps"
BLOCK = 100  # Spiele pro Anfrage (getestet: 278 gehen auch, 100 hält die URL kurz)


class SteamFehler(Exception):
    """Meldung ist für Rico gedacht."""


@dataclass
class SteamSpiel:
    appid: int
    name: str
    spielzeit_min: int
    spielzeit_2wochen_min: int
    zuletzt_gespielt: dt.datetime | None


def cover_url(appid: int) -> str:
    """Hochformat-Cover (fehlt bei manchen älteren Spielen → siehe cover_ersatz_url)."""
    return f"{CDN}/{appid}/library_600x900.jpg"


def cover_ersatz_url(appid: int) -> str:
    """Querformat-Bild, gibt es praktisch immer."""
    return f"{CDN}/{appid}/header.jpg"


def _zahl(wert: object) -> int:
    return wert if isinstance(wert, int) and wert > 0 else 0


def spiele_auslesen(antwort: object) -> list[SteamSpiel]:
    inhalt = antwort.get("response") if isinstance(antwort, dict) else None
    if not isinstance(inhalt, dict):
        raise SteamFehler("Unerwartete Antwort von Steam.")
    if "games" not in inhalt:
        # So antwortet Steam, wenn die Spieldetails des Profils nicht öffentlich sind.
        raise SteamFehler(
            "Steam liefert keine Spiele. Sind die Spieldetails im Steam-Profil auf „Öffentlich“ gestellt?"
        )
    spiele = []
    for eintrag in inhalt["games"]:
        if not isinstance(eintrag, dict) or not isinstance(eintrag.get("appid"), int):
            continue
        name = str(eintrag.get("name") or "").strip() or f"Steam-App {eintrag['appid']}"
        zuletzt = _zahl(eintrag.get("rtime_last_played"))
        spiele.append(
            SteamSpiel(
                appid=eintrag["appid"],
                name=name[:300],
                spielzeit_min=_zahl(eintrag.get("playtime_forever")),
                spielzeit_2wochen_min=_zahl(eintrag.get("playtime_2weeks")),
                zuletzt_gespielt=dt.datetime.fromtimestamp(zuletzt) if zuletzt else None,
            )
        )
    return spiele


def _bloecke(appids: list[int]) -> Iterator[list[int]]:
    for i in range(0, len(appids), BLOCK):
        yield appids[i : i + BLOCK]


def _json_holen(client: httpx.Client, url: str, params: dict) -> dict:
    try:
        antwort = client.get(url, params=params)
    except httpx.HTTPError:
        raise SteamFehler("Steam ist gerade nicht erreichbar.") from None  # URL enthält evtl. den Key
    if antwort.status_code in (401, 403):
        raise SteamFehler("Steam lehnt den API-Key ab. Bitte STEAM_API_KEY in der .env prüfen.")
    if antwort.status_code != 200:
        raise SteamFehler(f"Steam antwortet mit Fehler {antwort.status_code}. Später nochmal versuchen.")
    try:
        daten = antwort.json()
    except ValueError:
        raise SteamFehler("Unerwartete Antwort von Steam.") from None
    if not isinstance(daten, dict) or not isinstance(daten.get("response"), dict):
        raise SteamFehler("Unerwartete Antwort von Steam.")
    return daten["response"]


@dataclass
class StoreBilder:
    cover_url: str | None  # Hochformat (library_capsule)
    header_url: str | None  # Querformat


def bilder_auslesen(eintraege: object) -> dict[int, StoreBilder]:
    """Neuere Spiele liegen auf dem CDN unter Pfaden mit Hash – die echten Dateinamen liefert nur der Store."""
    ergebnis: dict[int, StoreBilder] = {}
    for eintrag in eintraege if isinstance(eintraege, list) else []:
        if not isinstance(eintrag, dict) or not isinstance(eintrag.get("appid"), int):
            continue
        assets = eintrag.get("assets")
        if not isinstance(assets, dict) or not isinstance(assets.get("asset_url_format"), str):
            continue
        vorlage = assets["asset_url_format"]

        def url(feld: str, assets: dict = assets, vorlage: str = vorlage) -> str | None:
            datei = assets.get(feld)
            if not isinstance(datei, str) or not datei or ".." in datei or "${FILENAME}" not in vorlage:
                return None
            return ASSETS + vorlage.replace("${FILENAME}", datei)

        ergebnis[eintrag["appid"]] = StoreBilder(cover_url=url("library_capsule"), header_url=url("header"))
    return ergebnis


def store_bilder(appids: list[int], client: httpx.Client) -> dict[int, StoreBilder]:
    """Bildadressen aus dem Steam-Store (öffentlich, ohne Key). Spiele ohne Store-Seite fehlen im Ergebnis."""
    ergebnis: dict[int, StoreBilder] = {}
    for block in _bloecke(appids):
        eingabe = {
            "ids": [{"appid": a} for a in block],
            "context": {"language": "german", "country_code": "DE"},
            "data_request": {"include_assets": True},
        }
        antwort = _json_holen(client, URL_STORE, {"input_json": json.dumps(eingabe)})
        ergebnis.update(bilder_auslesen(antwort.get("store_items")))
    return ergebnis


@dataclass
class Erfolge:
    erreicht: int
    gesamt: int  # 0 = Spiel hat keine Erfolge


def erfolge_auslesen(spiele: object) -> dict[int, Erfolge]:
    ergebnis: dict[int, Erfolge] = {}
    for eintrag in spiele if isinstance(spiele, list) else []:
        if not isinstance(eintrag, dict) or not isinstance(eintrag.get("appid"), int):
            continue
        gesamt = _zahl(eintrag.get("total_achievements"))
        liste = eintrag.get("achievements")
        erreicht = len(liste) if isinstance(liste, list) else 0
        ergebnis[eintrag["appid"]] = Erfolge(erreicht=min(erreicht, gesamt), gesamt=gesamt)
    return ergebnis


def erfolge(api_key: str, steam_id: str, appids: list[int], client: httpx.Client) -> dict[int, Erfolge]:
    """Erreichte/gesamte Erfolge pro Spiel. Liefert die freigeschalteten Erfolge als Liste – daher hohes Maximum."""
    ergebnis: dict[int, Erfolge] = {}
    for block in _bloecke(appids):
        params: dict[str, object] = {"key": api_key, "steamid": steam_id, "max_achievements": 10000}
        params.update({f"appids[{i}]": a for i, a in enumerate(block)})
        ergebnis.update(erfolge_auslesen(_json_holen(client, URL_ERFOLGE, params).get("games")))
    return ergebnis


def eigene_spiele(api_key: str, steam_id: str, client: httpx.Client) -> list[SteamSpiel]:
    params = {
        "key": api_key,
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    }
    return spiele_auslesen({"response": _json_holen(client, URL, params)})
