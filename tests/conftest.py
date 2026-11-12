import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from digitallibrary.config import Einstellungen
from digitallibrary.db import engine_erstellen, migrieren
from digitallibrary.web import app_erstellen

DATEN = Path(__file__).parent / "daten"
DUNE_ISBN = "9783453317178"


@pytest.fixture
def db_pfad(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def engine(db_pfad: Path):
    engine = engine_erstellen(db_pfad)
    migrieren(engine, db_pfad)
    yield engine
    engine.dispose()


@pytest.fixture
def sitzung(engine):
    with Session(engine) as s:
        yield s


@pytest.fixture
def openlibrary_antworten() -> dict[str, object]:
    """ISBN → JSON-Antwort. Echte Antwort für Dune (tests/daten), sonst wie im Original: {}."""
    return {DUNE_ISBN: json.loads((DATEN / "openlibrary_dune.json").read_text(encoding="utf-8"))}


@pytest.fixture
def steam_antwort() -> dict[str, object]:
    """Aufbau wie die echte GetOwnedGames-Antwort, Spielzeiten erfunden. Im Test änderbar."""
    return {"antwort": json.loads((DATEN / "steam_owned.json").read_text(encoding="utf-8"))}


@pytest.fixture
def steam_store() -> dict[str, object]:
    """Aufbau wie IStoreBrowseService/GetItems: Portal mit klassischen, Hades mit Hash-Pfaden, Stardew ohne Store."""
    return {"antwort": {"response": {"store_items": [
        {"item_type": 0, "id": 400, "success": 1, "visible": True, "name": "Portal", "appid": 400,
         "assets": {"asset_url_format": "steam/apps/400/${FILENAME}?t=1745368554",
                    "header": "header.jpg", "library_capsule": "library_600x900.jpg"}},
        {"item_type": 0, "id": 1145360, "success": 1, "visible": True, "name": "Hades", "appid": 1145360,
         "assets": {"asset_url_format": "steam/apps/1145360/${FILENAME}?t=1",
                    "header": "aaa111/header.jpg", "library_capsule": "bbb222/library_capsule.jpg"}},
        {"item_type": 0, "id": 413150, "success": 15},
    ]}}}  # fmt: skip


@pytest.fixture
def steam_erfolge() -> dict[str, object]:
    """Aufbau wie IPlayerService/GetTopAchievementsForGames (Liste = freigeschaltete Erfolge)."""
    erfolg = {"statid": 0, "bit": 1, "name": "Erfolg", "desc": "", "icon": "x.jpg", "icon_gray": "y.jpg",
              "hidden": False, "player_percent_unlocked": "50.0"}  # fmt: skip
    return {"antwort": {"response": {"games": [
        {"appid": 400, "total_achievements": 15, "achievements": [erfolg] * 15},
        {"appid": 1145360, "total_achievements": 49, "achievements": [erfolg] * 12},
        {"appid": 413150},
    ]}}}  # fmt: skip


@pytest.fixture
def anfragen() -> list[httpx.Request]:
    return []


@pytest.fixture
def http_client(openlibrary_antworten, steam_antwort, steam_store, steam_erfolge, anfragen) -> httpx.Client:
    def antworten(request: httpx.Request) -> httpx.Response:
        anfragen.append(request)
        if request.url.host == "api.steampowered.com":
            if "GetItems" in request.url.path:
                antwort = steam_store["antwort"]
            elif "GetTopAchievementsForGames" in request.url.path:
                antwort = steam_erfolge["antwort"]
            else:
                antwort = steam_antwort["antwort"]
        else:
            isbn = request.url.params["bibkeys"].removeprefix("ISBN:")
            antwort = openlibrary_antworten.get(isbn, {})
        if isinstance(antwort, httpx.Response):
            return antwort
        return httpx.Response(200, json=antwort)

    return httpx.Client(transport=httpx.MockTransport(antworten))


TEST_KEY = "0123456789ABCDEF0123456789ABCDEF"  # Fantasiewert
TEST_STEAM_ID = "76561190000000001"  # Fantasiewert


@pytest.fixture
def einstellungen(db_pfad) -> Einstellungen:
    # _env_file=None: Tests lesen nie die echte .env (dort steht Ricos API-Key)
    return Einstellungen(_env_file=None, datenbank=db_pfad, steam_api_key=TEST_KEY, steam_id=TEST_STEAM_ID)


@pytest.fixture
def client(engine, einstellungen, http_client) -> TestClient:
    app = app_erstellen(einstellungen, engine=engine, http_client=http_client)
    return TestClient(app, follow_redirects=False)
