"""Text normalization shared by matching and verification."""

import unicodedata

QUOTES = str.maketrans("\u2018\u2019\u201c\u201d", "''\"\"")


def name_key(name: str) -> str:
    """Comparison key ignoring case, whitespace, and dash or quote variants.

    Models often swap ASCII hyphens for U+2011 and straight quotes for curly
    ones when copying text.
    """
    text = unicodedata.normalize("NFKC", name).translate(QUOTES)
    text = "".join("-" if unicodedata.category(ch) == "Pd" else ch for ch in text)
    return " ".join(text.split()).casefold()
