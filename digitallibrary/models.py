"""Datenmodell: gemeinsame Tabelle `medien`, dazu `spiele` und `buecher` (Joined Table Inheritance).

Schema-Änderungen nur über Alembic-Migrationen (digitallibrary/migrations), nie durch Löschen der DB.
"""

from __future__ import annotations

import datetime as dt
import enum
import zlib

from sqlalchemy import (
    CheckConstraint,
    Column,
    Enum,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from . import steam

# Feste Namen für Constraints – nötig, damit Alembic sie unter SQLite (Batch-Modus) ändern kann.
NAMENSKONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def jetzt() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMENSKONVENTION)


class Art(enum.StrEnum):
    SPIEL = "spiel"
    BUCH = "buch"


class Status(enum.StrEnum):
    BACKLOG = "backlog"
    LAEUFT = "laeuft"
    BEENDET = "beendet"
    ABGEBROCHEN = "abgebrochen"

    @property
    def label(self) -> str:
        return {
            Status.BACKLOG: "Backlog",
            Status.LAEUFT: "Läuft",
            Status.BEENDET: "Beendet",
            Status.ABGEBROCHEN: "Abgebrochen",
        }[self]


# Reihenfolge in Listen: was gerade läuft, steht oben.
STATUS_REIHENFOLGE = [Status.LAEUFT, Status.BACKLOG, Status.BEENDET, Status.ABGEBROCHEN]

medium_stimmung = Table(
    "medium_stimmung",
    Base.metadata,
    Column("medium_id", ForeignKey("medien.id", ondelete="CASCADE"), primary_key=True),
    Column("stimmung_id", ForeignKey("stimmungen.id", ondelete="CASCADE"), primary_key=True),
)


class Stimmung(Base):
    __tablename__ = "stimmungen"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class Medium(Base):
    __tablename__ = "medien"

    id: Mapped[int] = mapped_column(primary_key=True)
    art: Mapped[str] = mapped_column(String(10))
    titel: Mapped[str] = mapped_column(String(300))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[Status] = mapped_column(
        Enum(Status, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
        default=Status.BACKLOG,
    )
    bewertung: Mapped[int | None]
    notiz: Mapped[str] = mapped_column(Text, default="")
    # Geschätzte Gesamtdauer in Minuten, von Hand gepflegt (bei Büchern sonst aus Seitenzahl geschätzt).
    dauer_min: Mapped[int | None]
    hinzugefuegt_am: Mapped[dt.datetime] = mapped_column(default=jetzt)
    begonnen_am: Mapped[dt.date | None]
    beendet_am: Mapped[dt.date | None]
    geaendert_am: Mapped[dt.datetime] = mapped_column(default=jetzt, onupdate=jetzt)

    stimmungen: Mapped[list[Stimmung]] = relationship(
        secondary=medium_stimmung, order_by=lambda: func.lower(Stimmung.name)
    )
    fortschritt: Mapped[list[Fortschritt]] = relationship(
        back_populates="medium", cascade="all, delete-orphan", order_by="Fortschritt.zeitpunkt"
    )

    __table_args__ = (CheckConstraint("bewertung BETWEEN 1 AND 10", name="bewertung_bereich"),)
    __mapper_args__ = {"polymorphic_on": "art"}  # noqa: RUF012

    @property
    def geschaetzte_dauer_min(self) -> int | None:
        return self.dauer_min

    @property
    def untertitel(self) -> str:
        return ""

    @property
    def cover_ersatz_url(self) -> str | None:
        """Bild, falls cover_url nicht lädt (z. B. Steam-Spiel ohne Hochformat-Cover)."""
        return None

    @property
    def farbton(self) -> int:
        """Fester Farbton (0–359) aus dem Titel – für gezeichnete Cover und Buchrücken."""
        return zlib.crc32(self.titel.encode("utf-8")) % 360


class Spiel(Medium):
    __tablename__ = "spiele"

    id: Mapped[int] = mapped_column(ForeignKey("medien.id", ondelete="CASCADE"), primary_key=True)
    steam_appid: Mapped[int | None] = mapped_column(unique=True)
    spielzeit_min: Mapped[int] = mapped_column(default=0)
    zuletzt_gespielt: Mapped[dt.datetime | None]
    # Querformat-Bild aus dem Steam-Store (Ersatz, falls das Hochformat-Cover fehlt)
    header_url: Mapped[str | None] = mapped_column(String(500))
    # Steam-Erfolge. erfolge_gesamt: None = noch nicht abgefragt, 0 = Spiel hat keine Erfolge
    erfolge_erreicht: Mapped[int | None]
    erfolge_gesamt: Mapped[int | None]

    # inline: bei Abfragen über Medium werden die Spiel-Spalten gleich mitgeladen
    __mapper_args__ = {"polymorphic_identity": Art.SPIEL.value, "polymorphic_load": "inline"}  # noqa: RUF012

    @property
    def cover_ersatz_url(self) -> str | None:
        if self.header_url:
            return self.header_url
        if self.steam_appid is None:
            return None
        return steam.cover_ersatz_url(self.steam_appid)

    @property
    def hat_erfolge(self) -> bool:
        return bool(self.erfolge_gesamt)

    @property
    def erfolge_prozent(self) -> int | None:
        if not self.erfolge_gesamt:
            return None
        return int((self.erfolge_erreicht or 0) * 100 / self.erfolge_gesamt)

    @property
    def untertitel(self) -> str:
        if not self.spielzeit_min:
            return "noch nicht gespielt"
        return f"{self.spielzeit_min / 60:.1f} h gespielt".replace(".", ",")


class Buch(Medium):
    __tablename__ = "buecher"

    MINUTEN_PRO_SEITE = 1.5

    id: Mapped[int] = mapped_column(ForeignKey("medien.id", ondelete="CASCADE"), primary_key=True)
    isbn13: Mapped[str | None] = mapped_column(String(13), unique=True)
    autoren: Mapped[str] = mapped_column(String(500), default="")
    verlag: Mapped[str] = mapped_column(String(200), default="")
    jahr: Mapped[int | None]
    seiten: Mapped[int | None]
    aktuelle_seite: Mapped[int | None]
    openlibrary_key: Mapped[str | None] = mapped_column(String(50))
    reihe: Mapped[str] = mapped_column(String(200), default="", server_default="")
    reihe_nr: Mapped[int | None]

    __mapper_args__ = {"polymorphic_identity": Art.BUCH.value, "polymorphic_load": "inline"}  # noqa: RUF012

    @property
    def geschaetzte_dauer_min(self) -> int | None:
        if self.dauer_min is not None:
            return self.dauer_min
        if self.seiten:
            return round(self.seiten * self.MINUTEN_PRO_SEITE)
        return None

    @property
    def untertitel(self) -> str:
        return self.autoren


class Fortschritt(Base):
    """Momentaufnahme: Spielminuten (Spiel) bzw. aktuelle Seite (Buch) zu einem Zeitpunkt."""

    __tablename__ = "fortschritt"

    id: Mapped[int] = mapped_column(primary_key=True)
    medium_id: Mapped[int] = mapped_column(ForeignKey("medien.id", ondelete="CASCADE"), index=True)
    zeitpunkt: Mapped[dt.datetime] = mapped_column(default=jetzt)
    wert: Mapped[int]

    medium: Mapped[Medium] = relationship(back_populates="fortschritt")
