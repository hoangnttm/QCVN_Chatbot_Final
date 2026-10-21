"""Deterministic text normalisation shared by the ingestion pipeline and the bootstrap script.

Nothing here changes legal meaning: we only flatten converter artefacts (inline LaTeX, Markdown
emphasis, legacy-font characters) so tokenisers, dictionaries and value parsers see plain text.
The raw line is always kept alongside (PLAN.md F6, F10).
"""

import re
import unicodedata

_LATEX_TEXT = re.compile(r"\\(?:text|mathrm)\{([^}]*)\}")
_LATEX_SUBSUP_BRACED = re.compile(r"[_^]\{([^}]*)\}")
_LATEX_SUBSUP_SINGLE = re.compile(r"[_^](\S)")
_WS = re.compile(r"\s+")

# Legacy TCVN3/VNI-style code points that some PDF fonts emit instead of the proper letters.
LEGACY_CHARS = str.maketrans(
    {
        "ƣ": "ư",
        "Ƣ": "Ư",
        "\u01a1": "ơ",  # ơ (already correct, listed for completeness)
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def latex_to_text(s: str) -> str:
    """Flatten inline LaTeX: `$\\text{mg/Nm}^3$` -> `mg/Nm3`, `$\\le 40$` -> `≤ 40`, `$6 - 9$` -> `6 - 9`."""
    s = s.replace("\\text{ngày}", "ngày")
    s = _LATEX_TEXT.sub(r"\1", s)
    s = s.replace("^\\circ", "°").replace("\\circ", "°")
    s = s.replace("\\le", "≤").replace("\\ge", "≥").replace("\\beta", "β")
    s = _LATEX_SUBSUP_BRACED.sub(r"\1", s)
    s = _LATEX_SUBSUP_SINGLE.sub(r"\1", s)
    s = s.replace("\\,", "").replace("\\ ", " ").replace("\\%", "%")
    s = s.replace("$", "").replace("{", "").replace("}", "")
    return _WS.sub(" ", s).strip()


def strip_md(s: str) -> str:
    """Remove Markdown emphasis markers (**bold**, *italic*) and ATX heading markers (###)."""
    s = re.sub(r"(?m)^\s*#{1,6}\s+", "", s)
    return re.sub(r"\*\*|\*", "", s).strip()


def plain(s: str) -> str:
    """strip_md + latex_to_text — the canonical 'plain text' form used for matching and embedding."""
    return latex_to_text(strip_md(s))


def ascii_slug(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def normalize_legacy_text(s: str) -> str:
    """Repair legacy-font characters from PDF extraction (`nƣớc` -> `nước`) and NFC-normalise."""
    return unicodedata.normalize("NFC", s.translate(LEGACY_CHARS))


_METHOD_PREFIX = re.compile(r"\b(SMEWW|TCVN|ISO|ASTM|EPA|ES|IS|JIS|BS|EN)\s*(?=\d)")


def normalize_method_code(s: str) -> str:
    """'SMEWW2550B:2017' -> 'SMEWW 2550B:2017'; 'SMEWW4500 H + .B:2017' -> 'SMEWW 4500 H+.B:2017'."""
    s = _METHOD_PREFIX.sub(r"\1 ", s)
    s = re.sub(r"\s*\+\s*", "+", s)
    s = re.sub(r"\s*\.\s*(?=[A-Z]\b)", ".", s)
    s = re.sub(r"\s+", " ", s).strip(" ;,.")
    return s


_VN_NUMBER = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^-?\d+(?:,\d+)?$|^-?\d+(?:\.\d+)?$")


def parse_vn_number(token: str) -> float | None:
    """Parse a Vietnamese-formatted number: '0,05' -> 0.05, '12.380.000' -> 12380000, '2000' -> 2000."""
    t = token.strip().replace(" ", "")
    if not t or not _VN_NUMBER.match(t):
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None
