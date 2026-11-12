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
def anfragen() -> list[httpx.Request]:
    return []


@pytest.fixture
def http_client(openlibrary_antworten, anfragen) -> httpx.Client:
    def antworten(request: httpx.Request) -> httpx.Response:
        anfragen.append(request)
        isbn = request.url.params["bibkeys"].removeprefix("ISBN:")
        antwort = openlibrary_antworten.get(isbn, {})
        if isinstance(antwort, httpx.Response):
            return antwort
        return httpx.Response(200, json=antwort)

    return httpx.Client(transport=httpx.MockTransport(antworten))


@pytest.fixture
def client(engine, db_pfad, http_client) -> TestClient:
    app = app_erstellen(Einstellungen(datenbank=db_pfad), engine=engine, http_client=http_client)
    return TestClient(app, follow_redirects=False)
