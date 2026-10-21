"""Raw-source access for gold anchoring: read the registered file in `sources/` (Markdown lines
or the docling text of a PDF), verify the sha256 recorded in the registry, check quotes against
the raw lines, and map a `SourceRef` to the derived chunks whose span covers it.

Nothing here goes through `app/ingestion`: this is the independent side of the gold ↔ parser
comparison (plan §2.0)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.evaluation.gold_schema import SourceRef
from app.registry.loader import load_registry, resolve_source_path

_WS = re.compile(r"\s+")
_LATEX_TEXT = re.compile(r"\\text\{([^}]*)\}")
_DEGREE = re.compile(r"(\d)\s*[oº°]\s*c\b")
_LATEX_CMD = {
    r"\le": "≤",
    r"\leq": "≤",
    r"\ge": "≥",
    r"\geq": "≥",
    r"\times": "×",
    r"\circ": "°",
    r"^\circ": "°",
    r"\%": "%",
}


def normalize_raw(text: str) -> str:
    """Whitespace/LaTeX/markdown-insensitive normal form used for quote checks."""
    t = unicodedata.normalize("NFC", text or "")
    t = _LATEX_TEXT.sub(r"\1", t)
    for k, v in _LATEX_CMD.items():
        t = t.replace(k, v)
    t = t.replace("$", "").replace("<br>", " ").replace("**", "").replace("*", "")
    t = t.replace("_", "").replace("{", "").replace("}", "").replace("^", "")
    t = t.replace("–", "-").replace("—", "-").replace("³", "3").replace("²", "2")
    t = t.replace("\u00a0", " ")
    t = _WS.sub(" ", t).strip().lower()
    return _DEGREE.sub(r"\1°c", t)  # "25 oC", "25 ºC" and "25°C" are one reading


_PUNCT = re.compile(r"[|:;,.()\[\]\"'“”«»\-/]+")
_ELLIPSIS = re.compile(r"\.\.\.|…")


_CMP = re.compile(r"\s*(≤|≥|<=|>=|<|>|=)\s*")
_CMP_ASCII = {"<=": "≤", ">=": "≥"}


def loose(text: str) -> str:
    """`normalize_raw`, then punctuation-insensitive: table pipes, colons, quotes and dashes
    are not part of what an author copies ("C 10: Amoni" against "| C 10 | Amoni |"), and
    "≤40", "≤ 40" and "<= 40" are one reading."""
    t = _CMP.sub(lambda m: f" {_CMP_ASCII.get(m.group(1), m.group(1))} ", normalize_raw(text))
    return _WS.sub(" ", _PUNCT.sub(" ", t)).strip()


def quote_fragments(quote: str) -> list[str]:
    """An author may skip words with "..." or "…": the verbatim fragments, in order, in loose
    form. A quote without an ellipsis is one fragment."""
    return [f for f in (loose(p) for p in _ELLIPSIS.split(quote or "")) if f]


def quote_matches(quote: str, haystack: str) -> bool:
    """True when the quote is in the haystack: verbatim, or as verbatim fragments around
    ellipses that appear in the same order; whitespace, LaTeX, markdown and punctuation do
    not count."""
    frags = quote_fragments(quote)
    if not frags:
        return False
    hay = loose(haystack)
    pos = 0
    for f in frags:
        i = hay.find(f, pos)
        if i < 0:
            return False
        pos = i + len(f)
    return True


def nearest_line(lines: list[str], quote: str, *, min_len: int = 15) -> tuple[int, str] | None:
    """(1-based line number, raw line) of the line most similar to a quote that was not found,
    for the message that hands the row back to the author; None when nothing comes close."""
    import difflib

    want = loose(quote)
    if not want:
        return None
    best: tuple[float, int, str] | None = None
    sm = difflib.SequenceMatcher(None, want, "")
    for i, line in enumerate(lines, start=1):
        if len(line) < min_len:
            continue
        sm.set_seq2(loose(line))
        if best is not None and sm.quick_ratio() <= best[0]:
            continue
        r = sm.ratio()
        if best is None or r > best[0]:
            best = (r, i, line)
    return (best[1], best[2]) if best is not None and best[0] >= 0.4 else None


@dataclass(frozen=True)
class SourceFile:
    document_id: str
    path: Path
    sha256: str
    fmt: str
    line_range: tuple[int, int] | None


class RawSource:
    """Registry-driven reader of the immutable files in `sources/`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = load_registry(settings=self.settings)
        self.files: dict[str, SourceFile] = {}
        for doc in self.registry.documents:
            if not doc.source:
                continue
            p = resolve_source_path(doc.source.file, self.settings)
            if p is None:
                continue
            self.files[doc.document_id] = SourceFile(
                doc.document_id, p, doc.source.sha256, doc.source.format, doc.source.line_range
            )
        self._lines: dict[str, list[str]] = {}
        self._hash_ok: dict[str, bool] = {}

    # ------------------------------------------------------------ files

    def file(self, document_id: str) -> SourceFile:
        try:
            return self.files[document_id]
        except KeyError as e:
            raise KeyError(f"no registered source for {document_id}") from e

    def sha256_ok(self, document_id: str) -> bool:
        if document_id not in self._hash_ok:
            f = self.file(document_id)
            h = hashlib.sha256()
            with open(f.path, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            self._hash_ok[document_id] = h.hexdigest() == f.sha256
        return self._hash_ok[document_id]

    def lines(self, document_id: str) -> list[str]:
        """1-based addressable lines of the raw text. Markdown: the file itself. PDF: the docling
        Markdown in `data/derived/<doc>/pdf_extract/extracted.md` (best available raw text)."""
        if document_id not in self._lines:
            f = self.file(document_id)
            if f.fmt == "md":
                text = f.path.read_text(encoding="utf-8")
            else:
                p = (
                    self.settings.data_dir
                    / "derived"
                    / document_id
                    / "pdf_extract"
                    / "extracted.md"
                )
                text = p.read_text(encoding="utf-8") if p.exists() else ""
            self._lines[document_id] = text.split("\n")
        return self._lines[document_id]

    def slice(self, ref: SourceRef) -> list[str]:
        lines = self.lines(ref.document_id)
        return lines[ref.line_start - 1 : ref.line_end]

    def quote_in(self, ref: SourceRef, quote: str) -> bool:
        """The quote (verbatim, or fragments around ellipses) is in the referenced lines."""
        return quote_matches(quote, " ".join(self.slice(ref)))

    def find(
        self, document_id: str, needle: str, *, within: tuple[int, int] | None = None
    ) -> list[int]:
        """Line numbers (1-based) whose text contains the needle (see `quote_matches`)."""
        out = []
        for i, line in enumerate(self.lines(document_id), start=1):
            if within and not (within[0] <= i <= within[1]):
                continue
            if quote_matches(needle, line):
                out.append(i)
        return out


# ---------------------------------------------------------------- derived (parser output)


@lru_cache(maxsize=16)
def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class Derived:
    """Read-only view of `data/derived/<doc>/*.json` (mirror of the PostgreSQL tables)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = self.settings.data_dir / "derived"

    def documents(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if (p / "chunks.json").exists())

    def _doc(self, document_id: str, name: str) -> Any:
        p = self.root / document_id / f"{name}.json"
        if not p.exists():
            return None
        return _load_json(str(p))

    def chunks(self, document_id: str) -> list[dict[str, Any]]:
        d = self._doc(document_id, "chunks")
        if d is None:
            return []
        return d["chunks"] if isinstance(d, dict) else d

    def tables(self, document_id: str) -> list[dict[str, Any]]:
        d = self._doc(document_id, "tables")
        if d is None:
            return []
        return d["tables"] if isinstance(d, dict) else d

    def rules(self, document_id: str) -> list[dict[str, Any]]:
        d = self._doc(document_id, "rules")
        if d is None:
            return []
        return d["rules"] if isinstance(d, dict) else d

    def structure(self, document_id: str) -> list[dict[str, Any]]:
        d = self._doc(document_id, "structure")
        if d is None:
            return []
        return d["nodes"] if isinstance(d, dict) else d

    def monitoring(self, document_id: str) -> list[dict[str, Any]]:
        d = self._doc(document_id, "monitoring")
        if d is None:
            return []
        return (
            d["requirements"]
            if isinstance(d, dict) and "requirements" in d
            else (d if isinstance(d, list) else d.get("items", []))
        )

    def map_to_chunks(
        self, ref: SourceRef, *, chunk_types: tuple[str, ...] | None = None
    ) -> list[dict[str, Any]]:
        """Chunks of the document whose span covers the referenced lines (same file hash),
        narrowest span first. Table rows: the row chunk whose span contains the line."""
        out = []
        for c in self.chunks(ref.document_id):
            sp = c.get("span") or {}
            if sp.get("file_hash") != ref.file_sha256:
                continue
            if chunk_types and c.get("chunk_type") not in chunk_types:
                continue
            ls, le = sp.get("line_start"), sp.get("line_end")
            if ls is None or le is None:
                continue
            if ls <= ref.line_start and ref.line_end <= le:
                out.append(c)
        out.sort(key=lambda c: (c["span"]["line_end"] - c["span"]["line_start"], c["chunk_id"]))
        return out

    def chunk_covers(self, chunk: dict[str, Any], ref: SourceRef) -> bool:
        sp = chunk.get("span") or {}
        return (
            sp.get("file_hash") == ref.file_sha256
            and sp.get("line_start") is not None
            and sp["line_start"] <= ref.line_start
            and ref.line_end <= sp["line_end"]
        )
