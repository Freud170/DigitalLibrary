"""ISBN prüfen und auf ISBN-13 vereinheitlichen.

Wichtig: Die Open Library liefert selbst für Unsinn wie 0000000000000 ein (falsches) Buch zurück.
Deshalb wird vor jeder Anfrage die Prüfziffer kontrolliert.
"""

import re


def _isbn13_pruefziffer(zwoelf: str) -> str:
    summe = sum(int(z) * (1 if i % 2 == 0 else 3) for i, z in enumerate(zwoelf))
    return str((10 - summe % 10) % 10)


def _isbn10_gueltig(isbn: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", isbn):
        return False
    summe = sum((10 - i) * (10 if z == "X" else int(z)) for i, z in enumerate(isbn))
    return summe % 11 == 0


def normalisieren(eingabe: str) -> str | None:
    """Gibt die ISBN-13 ohne Bindestriche zurück oder None, wenn die Eingabe keine gültige ISBN ist."""
    isbn = re.sub(r"[\s\-]", "", eingabe or "").upper()
    if len(isbn) == 10:
        if not _isbn10_gueltig(isbn):
            return None
        zwoelf = "978" + isbn[:9]
        return zwoelf + _isbn13_pruefziffer(zwoelf)
    if re.fullmatch(r"97[89]\d{10}", isbn) and _isbn13_pruefziffer(isbn[:12]) == isbn[12]:
        return isbn
    return None
