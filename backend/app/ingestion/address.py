"""Legal address of a piece of evidence (plan 2026-10-06, principle 3; G2 step 2.2).

The location of a provision or of a table cell in a legal text is its structural path — Chương,
Mục, Điều, Khoản, Điểm; Bảng, row (TT), column — never a page or a file line. The address is
mandatory and is the identifier of record; a page number travels with it as additional
information when the source has pages.

    [{"level": "Section", "number": "2"}, {"level": "Section", "number": "2.1"},
     {"level": "Table", "number": "1"}, {"level": "TableRow", "number": "1"},
     {"level": "Column", "key": "F>2000.B", "label": "F > 2000, Cột B"}]
"""

from __future__ import annotations

from typing import Any

from app.ingestion.models import Node

# structural kinds that are levels of a legal address (titles, headers, paragraphs are not)
ADDRESS_KINDS = (
    "Chapter",
    "Section",
    "Article",
    "Clause",
    "Point",
    "Definition",
    "Appendix",
    "AppendixSection",
    "Table",
    "AppendixTable",
    "Footnote",
    "Preamble",
)


def node_address(node_id: str, by_id: dict[str, Node]) -> list[dict[str, Any]]:
    """Path of structural levels from the top of the document down to `node_id`."""
    parts: list[dict[str, Any]] = []
    cur = by_id.get(node_id)
    while cur is not None:
        if cur.kind in ADDRESS_KINDS:
            part: dict[str, Any] = {"level": cur.kind}
            if cur.number:
                part["number"] = cur.number
            if cur.label:
                part["label"] = cur.label
            parts.append(part)
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    return list(reversed(parts))


def cell_address(
    table_node_id: str,
    by_id: dict[str, Node],
    row_no: str,
    column_key: str,
    column_label: str | None,
) -> list[dict[str, Any]]:
    """Address of one value cell: the table's path, then its row and its column."""
    column: dict[str, Any] = {"level": "Column", "key": column_key}
    if column_label:
        column["label"] = column_label
    return [
        *node_address(table_node_id, by_id),
        {"level": "TableRow", "number": row_no},
        column,
    ]


# levels that are unique in a document and so need no number ("Lời nói đầu")
SINGLETON_LEVELS = ("Preamble", "Registry")


def is_resolvable(address: list[dict[str, Any]]) -> bool:
    """A usable address names at least one numbered or labelled structural level, or a level
    that exists once per document."""
    return any(
        p.get("number") or p.get("label") or p.get("key") or p.get("level") in SINGLETON_LEVELS
        for p in address
    )
