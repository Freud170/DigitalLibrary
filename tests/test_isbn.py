import pytest

from digitallibrary.isbn import normalisieren


@pytest.mark.parametrize(
    ("eingabe", "erwartet"),
    [
        ("9783453317178", "9783453317178"),
        ("978-3-453-31717-8", "9783453317178"),
        (" 978 3 453 31717 8 ", "9783453317178"),
        ("3453317173", "9783453317178"),  # ISBN-10 → ISBN-13
        ("0306406152", "9780306406157"),
        ("080442957X", "9780804429573"),  # Prüfziffer X
        ("080442957x", "9780804429573"),
    ],
)
def test_gueltige_isbn(eingabe, erwartet):
    assert normalisieren(eingabe) == erwartet


@pytest.mark.parametrize(
    "eingabe",
    [
        "",
        "abc",
        "9783453317179",  # falsche Prüfziffer
        "3453317174",
        "0000000000000",  # Open Library liefert hierfür trotzdem ein Buch!
        "1234567890123",  # kein 978/979-Präfix
        "97834533171780",
        "X804429570",
    ],
)
def test_ungueltige_isbn(eingabe):
    assert normalisieren(eingabe) is None
