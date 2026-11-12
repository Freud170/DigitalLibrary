"""Steam-Bibliothek über die Steam Web API (IPlayerService/GetOwnedGames).

Der API-Key steht nur in der .env und wird nie ausgegeben oder geloggt.
"""

import datetime as dt
from dataclasses import dataclass

import httpx

URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
CDN = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps"


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


def eigene_spiele(api_key: str, steam_id: str, client: httpx.Client) -> list[SteamSpiel]:
    params = {
        "key": api_key,
        "steamid": steam_id,
        "include_appinfo": 1,
        "include_played_free_games": 1,
        "format": "json",
    }
    try:
        antwort = client.get(URL, params=params)
    except httpx.HTTPError:
        # Absichtlich ohne Details: die URL enthält den API-Key.
        raise SteamFehler("Steam ist gerade nicht erreichbar.") from None
    if antwort.status_code in (401, 403):
        raise SteamFehler("Steam lehnt den API-Key ab. Bitte STEAM_API_KEY in der .env prüfen.")
    if antwort.status_code != 200:
        raise SteamFehler(f"Steam antwortet mit Fehler {antwort.status_code}. Später nochmal versuchen.")
    try:
        daten = antwort.json()
    except ValueError:
        raise SteamFehler("Unerwartete Antwort von Steam.") from None
    return spiele_auslesen(daten)
