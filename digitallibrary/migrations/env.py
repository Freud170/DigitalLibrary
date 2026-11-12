"""Alembic-Umgebung. Normalfall: Aufruf aus db.migrieren() mit fertiger Verbindung.

Für neue Migrationen (im Projektordner):
    .\\.venv\\Scripts\\python.exe -m alembic revision --autogenerate -m "beschreibung"
"""

from alembic import context

from digitallibrary.config import Einstellungen
from digitallibrary.db import engine_erstellen
from digitallibrary.models import Base

config = context.config
verbindung = config.attributes.get("connection")


def _ausfuehren(conn) -> None:
    context.configure(connection=conn, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if verbindung is not None:
    _ausfuehren(verbindung)
else:
    engine = engine_erstellen(Einstellungen().datenbank_pfad())
    with engine.connect() as conn:
        _ausfuehren(conn)
