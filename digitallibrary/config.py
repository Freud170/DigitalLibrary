"""Einstellungen aus Umgebungsvariablen bzw. der Datei .env im Projektordner."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJEKT = Path(__file__).resolve().parent.parent


class Einstellungen(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJEKT / ".env", env_file_encoding="utf-8", extra="ignore")

    datenbank: Path = PROJEKT / "daten" / "bibliothek.db"
    # Nur lokal erreichbar. Wer das ändert, braucht vorher Login/HTTPS.
    host: str = "127.0.0.1"
    port: int = 8000

    steam_api_key: SecretStr | None = None
    steam_id: str | None = None

    def datenbank_pfad(self) -> Path:
        """Relative Pfade gelten relativ zum Projektordner, nicht zum aktuellen Arbeitsordner."""
        return self.datenbank if self.datenbank.is_absolute() else PROJEKT / self.datenbank
