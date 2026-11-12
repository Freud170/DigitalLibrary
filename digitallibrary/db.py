"""Datenbank: Engine, Migrationen beim Start, Sicherung vor Migrationen."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event

from .models import jetzt

MIGRATIONEN = Path(__file__).resolve().parent / "migrations"


def engine_erstellen(pfad: Path) -> Engine:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{pfad.as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_verbindung, _eintrag):  # pragma: no cover - trivial
        cursor = dbapi_verbindung.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONEN))
    return config


def _sichern(pfad: Path) -> Path:
    """Kopie der DB über die SQLite-Backup-API nach daten/sicherung/."""
    ziel = pfad.parent / "sicherung" / f"{pfad.stem}_{jetzt():%Y-%m-%d_%H-%M-%S}{pfad.suffix}"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(pfad) as quelle, sqlite3.connect(ziel) as kopie:
        quelle.backup(kopie)
    quelle.close()
    kopie.close()
    return ziel


def migrieren(engine: Engine, pfad: Path | None = None) -> Path | None:
    """Bringt das Schema auf den neuesten Stand. Gibt den Pfad der Sicherung zurück, falls eine nötig war."""
    config = _alembic_config()
    ziel = ScriptDirectory.from_config(config).get_current_head()
    sicherung = None
    with engine.connect() as verbindung:
        aktuell = MigrationContext.configure(verbindung).get_current_revision()
        if aktuell == ziel:
            return None
        if aktuell is not None and pfad is not None and pfad.exists():
            sicherung = _sichern(pfad)
        # Im Batch-Modus baut Alembic Tabellen neu – dabei dürfen keine Fremdschlüssel-Kaskaden greifen.
        verbindung.exec_driver_sql("PRAGMA foreign_keys=OFF")
        verbindung.commit()
        config.attributes["connection"] = verbindung
        command.upgrade(config, "head")
        verbindung.commit()
        verbindung.exec_driver_sql("PRAGMA foreign_keys=ON")
    return sicherung
