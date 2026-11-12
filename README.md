# DigitalLibrary

Gemeinsamer Backlog für Spiele und Bücher, damit die Frage „Was spiele oder lese ich als
Nächstes?“ schneller beantwortet ist. Läuft lokal im Browser.

## Stand

- **Bücher:** per ISBN hinzufügen (Daten und Cover von der Open Library, vor dem Speichern
  prüfbar) oder von Hand ohne ISBN.
- **Status** pro Medium (Backlog / Läuft / Beendet / Abgebrochen), Bewertung 1–10, Notiz,
  Stimmungen, geschätzte Dauer, aktuelle Seite. Status lässt sich direkt in der Liste ändern.
- **Spiele:** Tab vorhanden, der Steam-Import folgt.
- Geplant: „Was als Nächstes?“-Modus, Spielzeit- und Lese-Verlauf, Jahresstatistik.

## Einrichten (Windows, PowerShell)

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env   # erst für den Steam-Import ausfüllen
```

## Starten

```powershell
.\.venv\Scripts\python.exe -m digitallibrary
```

Dann <http://127.0.0.1:8000> öffnen. Die App ist nur auf diesem PC erreichbar.

Die Daten liegen in `daten\bibliothek.db` (von Git ignoriert). Vor jeder Schema-Migration
wird automatisch eine Sicherung in `daten\sicherung\` angelegt.

## Entwicklung

```powershell
.\.venv\Scripts\python.exe -m pytest              # Tests
.\.venv\Scripts\python.exe -m ruff check .        # Prüfung
.\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "beschreibung"   # neue Migration
```

Aufbau:

| Datei | Inhalt |
|---|---|
| `digitallibrary/models.py` | Datenmodell: `medien` + `spiele`/`buecher`, `stimmungen`, `fortschritt` |
| `digitallibrary/bibliothek.py` | Fachlogik (unabhängig von der Weboberfläche) |
| `digitallibrary/web.py` | Routen (FastAPI), Formular-Prüfung |
| `digitallibrary/openlibrary.py`, `isbn.py` | ISBN-Prüfung und Open-Library-Abfrage |
| `digitallibrary/db.py`, `migrations/` | Datenbank, Alembic-Migrationen, Sicherung |
| `digitallibrary/templates/`, `static/` | Jinja2-Templates, CSS, htmx 2.0.4 (lokal) |
