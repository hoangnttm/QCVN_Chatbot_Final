"""Step 4: deterministic, grammar-driven structure parser (PLAN.md §1.2, §5).

Line-oriented: every non-blank line inside the document's line range must end up in exactly one
node; anything else is reported in `coverage.uncovered` and fails the Phase 1 gate. No heuristics
beyond what the grammar states — if a document needs a new pattern, the grammar changes, not the
code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.grammar import HEADING_KINDS, Grammar, Rule
from app.ingestion.models import CoverageReport, Node, ParsedStructure, Span
from app.ingestion.sanitize import SanitizedText
from app.ingestion.textnorm import plain

NUMBERED_KINDS = {"Section", "Clause", "Point", "Definition", "Article", "Chapter", "Appendix"}
# Kinds that always hang directly off the Document when the rule gives no parent_kinds
ROOT_KINDS = {
    "Header",
    "Title",
    "Subtitle",
    "DocumentNumber",
    "DateLine",
    "Preamble",
    "Chapter",
    "Article",
    "Appendix",
}


@dataclass
class _Open:
    node: Node
    rule: Rule | None


class StructureParser:
    def __init__(self, grammar: Grammar, document_id: str, file_hash: str, grammar_path: str):
        self.g = grammar
        self.document_id = document_id
        self.file_hash = file_hash
        self.grammar_path = grammar_path
        self.nodes: list[Node] = []
        self.warnings: list[str] = []
        self._order = 0
        self._ids: set[str] = set()
        self._boiler = grammar.boilerplate_regexes
        self._rules = grammar.structure
        self._heading_rules = [r for r in self._rules if r.kind in HEADING_KINDS]

    # ------------------------------------------------------------------ public

    def parse(self, text: SanitizedText, line_range: tuple[int, int]) -> ParsedStructure:
        start, end = line_range
        end = min(end, len(text.lines))
        root = self._new_node("Document", start, end, raw="", parent=None, depth=0)
        stack: list[_Open] = [_Open(root, None)]
        covered: set[int] = set()
        no = start
        while no <= end:
            line = text.line(no)
            if not line.strip():
                no += 1
                continue

            # 1. boilerplate (nơi nhận, chữ ký, separators) — a block until the next blank line,
            #    or until the next heading when the grammar says so (docling output separates
            #    every recipient line with a blank line)
            if any(p.search(line) for p in self._boiler):
                if self.g.boilerplate_block == "until_next_heading":
                    last = self._extend_until_heading(text, no, end)
                else:
                    last = self._extend_until_blank(text, no, end)
                node = self._new_node(
                    "Boilerplate", no, last, raw=self._raw(text, no, last), parent=root, depth=1
                )
                covered.update(range(no, last + 1))
                no = last + 1
                continue

            # 2. first matching grammar rule wins (grammar order encodes priority)
            rule, m = self._match(line, stack)
            if rule is None or rule.catch_all:
                # continuation of a wrapping node (PDF: a sentence cut by a page break, possibly
                # with docling's blank separator in between) beats a catch-all rule
                top = stack[-1]
                prev_nonblank = self._prev_nonblank(text, no - 1, start)
                if top.rule and top.rule.text_may_wrap and top.node.span.line_end == prev_nonblank:
                    self._append_line(top.node, line, no)
                    covered.add(no)
                    no += 1
                    continue
                if rule is None:
                    self.warnings.append(f"line {no}: no rule matched: {line[:80]!r}")
                    no += 1
                    continue

            # 3. determine block extent
            last = no
            title_extra: str | None = None
            if rule.block == "until_blank":
                last = self._extend_until_blank(text, no, end)
            elif rule.block == "until_next_heading":
                last = self._extend_until_heading(text, no, end)
            elif rule.block == "table":
                last = self._extend_table(text, no, end)
            if rule.title_regex is not None:
                nxt = self._next_nonblank(text, last + 1, end)
                if nxt is not None:
                    tm = rule.title_regex.match(text.line(nxt))
                    if tm:
                        title_extra = tm.groupdict().get("title")
                        last = nxt
            # docling splits long headings ("## Điều 10." / "## Quan trắc chất lượng nước biển"):
            # absorb following heading-like lines that are not themselves a hard heading
            cont_re = rule.title_continuation_regex
            if cont_re is not None:
                parts: list[str] = []
                nxt = self._next_nonblank(text, last + 1, end)
                while nxt is not None and not self._is_hard_heading(text.line(nxt)):
                    cm = cont_re.match(text.line(nxt))
                    if not cm:
                        break
                    part = (cm.groupdict().get("title") or "").strip()
                    if rule.title_continuation_upper and part != part.upper():
                        break
                    parts.append(part)
                    last = nxt
                    nxt = self._next_nonblank(text, last + 1, end)
                if parts:
                    base_title = (m.groupdict().get("title") or title_extra or "").strip()
                    # a heading fragment that starts lower-case is the tail of the real title
                    if base_title and base_title[0].islower():
                        title_extra = " ".join([*parts, base_title])
                    else:
                        title_extra = " ".join([base_title, *parts]).strip()
            # optional skip lines right after the node (e.g. "(Ban hành kèm theo …)")
            if rule.skip_lines:
                nxt = self._next_nonblank(text, last + 1, end)
                while nxt is not None and any(
                    re.search(p, text.line(nxt)) for p in rule.skip_lines
                ):
                    last = nxt
                    nxt = self._next_nonblank(text, last + 1, end)

            # 4. find the parent on the stack
            parent_open = self._find_parent(stack, rule)
            # docling sometimes emits "## Điều 1." *before* "## Chương I": keep that empty article
            # and re-hang it under the chapter we are about to create
            deferred: _Open | None = None
            if rule.kind == "Chapter" and "reorder_article_before_chapter" in self.g.postprocess:
                top = stack[-1]
                if (
                    top.node.kind == "Article"
                    and not top.node.title
                    and not top.node.text
                    and not any(n.parent_id == top.node.node_id for n in self.nodes)
                ):
                    deferred = top
            del stack[stack.index(parent_open) + 1 :]
            gd = {k: (v.strip() if isinstance(v, str) else v) for k, v in m.groupdict().items()}
            node = self._new_node(
                rule.kind,
                no,
                last,
                raw=self._raw(text, no, last),
                parent=parent_open.node,
                depth=len(stack),
                number=gd.get("number"),
                label=gd.get("label"),
                title=title_extra
                if cont_re is not None and title_extra
                else (gd.get("title") or title_extra),
                term=gd.get("term"),
                text_group=gd.get("text"),
                rule=rule,
            )
            stack.append(_Open(node, rule))
            if deferred is not None:
                old_id = deferred.node.node_id
                deferred.node.parent_id = node.node_id
                deferred.node.node_id = f"{node.node_id}/Article{deferred.node.number}"
                deferred.node.depth = node.depth + 1
                stack.append(deferred)
                self.warnings.append(
                    f"postprocess: {old_id} re-parented under {node.node_id} (docling reading order)"
                )
            covered.update(range(no, last + 1))
            no = last + 1

        if "reorder_article_before_chapter" in self.g.postprocess:
            self._reorder_article_before_chapter()
        self._assign_citation_labels()
        nonblank = [n for n in range(start, end + 1) if text.line(n).strip()]
        uncovered = [(n, text.line(n)[:100]) for n in nonblank if n not in covered]
        return ParsedStructure(
            document_id=self.document_id,
            file_hash=self.file_hash,
            grammar_path=self.grammar_path,
            line_range=(start, end),
            nodes=self.nodes,
            coverage=CoverageReport(
                total_nonblank=len(nonblank),
                covered=len(nonblank) - len(uncovered),
                uncovered=uncovered,
            ),
            warnings=self.warnings,
        )

    # ------------------------------------------------------------------ matching helpers

    def _match(self, line: str, stack: list[_Open]) -> tuple[Rule | None, re.Match[str] | None]:
        past_top = any(o.node.kind in ("Chapter", "Article", "Appendix", "Section") for o in stack)
        for rule in self._rules:
            if rule.only_at_top and past_top:
                continue
            m = rule.regex.match(line)
            if m:
                return rule, m
        return None, None

    def _is_hard_heading(self, line: str) -> bool:
        """A line that opens a new numbered unit or boilerplate (never a title continuation)."""
        if any(p.search(line) for p in self._boiler):
            return True
        return any(r.regex.match(line) for r in self._heading_rules if r.kind != "Heading")

    def _reorder_article_before_chapter(self) -> None:
        """Repair docling reading-order quirks around article titles (auditable via warnings):

        1. an untitled Article whose first child is a bare Heading takes that heading as its title
           ("## Điều 1." … "## Chương I" … "## Phạm vi điều chỉnh");
        2. an Article whose title starts lower-case is the tail of a wrapped heading whose head was
           emitted just before it ("## Yêu cầu cơ bản … không" then "## Điều 30. khí xung quanh…").
        """
        removed: list[Node] = []
        for i, art in enumerate(self.nodes):
            if art.kind != "Article":
                continue
            if not art.title:
                first_child = next(
                    (n for n in self.nodes[i + 1 :] if n.parent_id == art.node_id), None
                )
                if first_child is not None and first_child.kind == "Heading":
                    art.title = first_child.title
                    art.span.line_end = max(art.span.line_end, first_child.span.line_end)
                    for n in self.nodes:
                        if n.parent_id == first_child.node_id:
                            n.parent_id = art.node_id
                    removed.append(first_child)
                    self.warnings.append(
                        f"postprocess: heading '{first_child.title}' became the title of {art.node_id}"
                    )
                    continue
            if art.title and art.title[0].islower() and i > 0:
                prev = self.nodes[i - 1]
                has_children = any(n.parent_id == prev.node_id for n in self.nodes)
                if prev.kind == "Heading" and prev.title and not has_children:
                    art.title = f"{prev.title} {art.title}"
                    removed.append(prev)
                    self.warnings.append(
                        f"postprocess: heading '{prev.title}' prepended to the title of {art.node_id}"
                    )
        for n in removed:
            self.nodes.remove(n)

    def _is_heading_or_boiler(self, line: str) -> bool:
        if any(p.search(line) for p in self._boiler):
            return True
        return any(r.regex.match(line) for r in self._heading_rules)

    def _extend_until_blank(self, text: SanitizedText, no: int, end: int) -> int:
        last = no
        while last + 1 <= end and text.line(last + 1).strip():
            last += 1
        return last

    def _extend_until_heading(self, text: SanitizedText, no: int, end: int) -> int:
        last = no
        while last + 1 <= end and not self._is_heading_or_boiler(text.line(last + 1)):
            last += 1
        # trim trailing blank lines
        while last > no and not text.line(last).strip():
            last -= 1
        return last

    def _extend_table(self, text: SanitizedText, no: int, end: int) -> int:
        """A table block = the title line (if the rule matched a title) + following pipe lines,
        allowing blank lines between title and grid but not inside the grid."""
        last = no
        nxt = last + 1
        if not text.line(no).lstrip().startswith("|"):
            nb = self._next_nonblank(text, nxt, end)
            if nb is None or not text.line(nb).lstrip().startswith("|"):
                return last
            nxt = nb
        while nxt <= end and text.line(nxt).lstrip().startswith("|"):
            last = nxt
            nxt += 1
        return last

    def _prev_nonblank(self, text: SanitizedText, no: int, start: int) -> int | None:
        while no >= start:
            if text.line(no).strip():
                return no
            no -= 1
        return None

    def _next_nonblank(self, text: SanitizedText, no: int, end: int) -> int | None:
        while no <= end:
            if text.line(no).strip():
                return no
            no += 1
        return None

    def _find_parent(self, stack: list[_Open], rule: Rule) -> _Open:
        if rule.kind in ROOT_KINDS and not rule.parent_kinds:
            return stack[0]
        if rule.parent_kinds:
            for open_ in reversed(stack):
                if open_.node.kind in rule.parent_kinds:
                    # same-kind siblings: a Section level 2 nests under level 1, level 1 under Document
                    if open_.node.kind == rule.kind and rule.level is not None:
                        if (open_.rule.level or 0) < rule.level:
                            return open_
                        continue
                    if open_.node.kind == rule.kind and rule.level is None:
                        continue
                    return open_
            return stack[0]
        # no parent constraint: attach to the innermost node that is not the same kind
        for open_ in reversed(stack):
            if open_.node.kind != rule.kind:
                return open_
        return stack[0]

    # ------------------------------------------------------------------ node helpers

    def _raw(self, text: SanitizedText, a: int, b: int) -> str:
        return "\n".join(text.line(n) for n in range(a, b + 1))

    def _new_node(
        self,
        kind: str,
        a: int,
        b: int,
        *,
        raw: str,
        parent: Node | None,
        depth: int,
        number: str | None = None,
        label: str | None = None,
        title: str | None = None,
        term: str | None = None,
        text_group: str | None = None,
        rule: Rule | None = None,
    ) -> Node:
        self._order += 1
        seg = f"{kind}{number or label or ''}" if (number or label) else f"{kind}#{self._order}"
        node_id = f"{parent.node_id}/{seg}" if parent else self.document_id
        # ids must be unique: a caption repeated on the next page ("Bảng 26" twice under the same
        # point) or a re-numbered list would otherwise collide and break provenance
        if node_id in self._ids:
            k = 2
            while f"{node_id}#{k}" in self._ids:
                k += 1
            node_id = f"{node_id}#{k}"
        self._ids.add(node_id)
        # heading-like rules (those with a `title` group) carry their content in `title`;
        # their `text` stays empty so "## Điều 1." is recognisable as an untitled article
        heading_rule = rule is not None and "title" in rule.regex.groupindex
        body = text_group if text_group is not None else (None if heading_rule else raw)
        node = Node(
            node_id=node_id,
            document_id=self.document_id,
            kind=kind,
            order=self._order,
            depth=depth,
            parent_id=parent.node_id if parent else None,
            number=number,
            label=label,
            title=plain(title) if title else None,
            term=plain(term) if term else None,
            text=plain(body) if body else None,
            raw=raw,
            span=Span(file_hash=self.file_hash, line_start=a, line_end=b),
            table_lines=[ln for ln in raw.split("\n") if ln.lstrip().startswith("|")]
            if rule and rule.block == "table"
            else None,
        )
        self.nodes.append(node)
        return node

    def _append_line(self, node: Node, line: str, no: int) -> None:
        node.raw = node.raw + "\n" + line
        node.text = ((node.text or "") + " " + plain(line)).strip()
        node.span.line_end = no

    # ------------------------------------------------------------------ citation labels

    def _assign_citation_labels(self) -> None:
        cfg = self.g.citation
        doc_label = cfg.get("document_label", self.document_id)
        hierarchical = bool(cfg.get("hierarchical_numbers", False))
        by_id = {n.node_id: n for n in self.nodes}
        for n in self.nodes:
            parts: list[str] = []
            chain: list[Node] = []
            cur: Node | None = n
            while cur is not None:
                chain.append(cur)
                cur = by_id.get(cur.parent_id) if cur.parent_id else None
            chain.reverse()
            for c in chain:
                tpl = cfg.get(c.kind)
                if not tpl or c.kind == "Document":
                    continue
                part = tpl.format(number=c.number or "", label=c.label or "", title=c.title or "")
                if (
                    hierarchical
                    and c.kind in NUMBERED_KINDS
                    and c.kind not in {"Appendix", "Article", "Chapter"}
                ):
                    # QCVN numbering is hierarchical ("Mục 2.2.1" implies Mục 2.2) — keep the deepest
                    parts = [p for p in parts if not p.startswith(tpl.split("{")[0])]
                parts.append(part)
            n.citation_label = ", ".join([doc_label, *parts]) if parts else None
