"""Start: .\\.venv\\Scripts\\python.exe -m digitallibrary  →  http://127.0.0.1:8000"""

import uvicorn

from .config import Einstellungen
from .web import app_erstellen


def main() -> None:
    einstellungen = Einstellungen()
    app = app_erstellen(einstellungen)
    uvicorn.run(app, host=einstellungen.host, port=einstellungen.port)


if __name__ == "__main__":
    main()
