"""Typed view of data/grammars/<document>.yaml (see data/grammars/README.md)."""

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

BlockType = Literal["line", "until_blank", "table", "until_next_heading"]

# Kinds that terminate an `until_next_heading` block and reset the stack to the document root
HEADING_KINDS = {
    "Title",
    "Subtitle",
    "Header",
    "Chapter",
    "Section",
    "Article",
    "Appendix",
    "Table",
    "Heading",  # unnumbered "## …" line (docling); stops blocks but never starts one via _is_hard_heading
}


class Rule(BaseModel):
    model_config = ConfigDict(extra="allow")  # documentation keys (note, priority_over…) are fine

    kind: str
    match: str
    block: BlockType = "line"
    level: int | None = None
    parent_kinds: list[str] | None = None
    title_from_next_line: str | None = None
    title_may_wrap: bool = False
    text_may_wrap: bool = False
    attach_to: str | None = None
    skip_lines: list[str] = Field(default_factory=list)
    catch_all: bool = Field(
        default=False,
        description="rule matches any line; a wrapping continuation of the open node wins over it",
    )
    title_continuation: str | None = Field(
        default=None,
        description="regex; following non-blank lines matching it (and no heading rule) extend the title",
    )
    title_continuation_upper: bool = Field(
        default=False,
        description="only absorb continuation lines written in capitals (chapter/appendix titles)",
    )
    only_at_top: bool = Field(
        default=False,
        description="rule applies only before the first Chapter/Article/Appendix (e.g. 'Căn cứ …' preamble)",
    )

    @property
    def title_continuation_regex(self) -> re.Pattern[str] | None:
        return re.compile(self.title_continuation) if self.title_continuation else None

    @field_validator("match")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v

    @property
    def regex(self) -> re.Pattern[str]:
        return re.compile(self.match)

    @property
    def title_regex(self) -> re.Pattern[str] | None:
        return re.compile(self.title_from_next_line) if self.title_from_next_line else None


class Grammar(BaseModel):
    model_config = ConfigDict(extra="allow")

    document_id: str
    source_format: Literal["md", "pdf"]
    line_range: tuple[int, int] | None = None
    preprocess: list[str] = Field(default_factory=list)
    structure: list[Rule]
    boilerplate: list[str] = Field(default_factory=list)
    boilerplate_block: Literal["until_blank", "until_next_heading"] = "until_blank"
    postprocess: list[str] = Field(
        default_factory=list,
        description="deterministic fix-ups for converter quirks, e.g. reorder_article_before_chapter",
    )
    tables: dict[str, dict[str, Any]] = Field(default_factory=dict)
    citation: dict[str, Any] = Field(default_factory=dict)
    relations_extract: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def boilerplate_regexes(self) -> list[re.Pattern[str]]:
        return [re.compile(p) for p in self.boilerplate]


def merge_grammar(base: dict[str, Any], own: dict[str, Any]) -> dict[str, Any]:
    """`own` on top of `base`: mappings (`tables`, `citation`, a table config) merge key by key,
    everything else — the `structure` rule list included — is replaced as a whole."""
    out = dict(base)
    for key, value in own.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_grammar(out[key], value)
        else:
            out[key] = value
    return out


def read_grammar(path: Path, _seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    """The grammar file with its `extends` chain resolved (path relative to the file). A new
    document of a known shape — a circular extracted by docling, say — states only what differs."""
    path = path.resolve()
    if path in _seen:
        raise ValueError(f"grammar extends itself: {' -> '.join(str(p) for p in (*_seen, path))}")
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    parent = raw.pop("extends", None)
    if parent:
        raw = merge_grammar(read_grammar(path.parent / parent, (*_seen, path)), raw)
    return raw


def load_grammar(path: Path) -> Grammar:
    return Grammar.model_validate(read_grammar(path))
