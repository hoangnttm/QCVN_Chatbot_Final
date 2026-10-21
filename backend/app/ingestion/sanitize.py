"""Step 2 of the pipeline: sanitise source lines *without* changing line numbering.

Noise lines (converter junk, PLAN.md F5) are blanked in place so every later span still refers to
the original 1-based line numbers of the immutable file in sources/.
"""

import re
from dataclasses import dataclass, field

from app.ingestion.textnorm import normalize_legacy_text
from app.registry.schema import SourceFile


@dataclass
class SanitizedText:
    lines: list[str]  # index 0 == line 1 of the original file
    blanked: list[int] = field(default_factory=list)  # 1-based line numbers removed as noise
    legacy_fixed: int = 0  # lines changed by normalize_legacy_text
    lone_bullets: int = 0  # empty "-" list items dropped (docling)

    def line(self, no: int) -> str:
        return self.lines[no - 1]


def sanitize(raw_text: str, source: SourceFile, preprocess: list[str]) -> SanitizedText:
    lines = raw_text.split("\n")
    out = SanitizedText(lines=[ln.rstrip("\r") for ln in lines])

    if "strip_noise_lines" in preprocess and source.noise_lines:
        a, b = source.noise_lines
        for no in range(a, b + 1):
            if 1 <= no <= len(out.lines) and out.lines[no - 1].strip():
                out.lines[no - 1] = ""
                out.blanked.append(no)

    if "normalize_legacy_text" in preprocess:
        for i, ln in enumerate(out.lines):
            fixed = normalize_legacy_text(ln)
            if fixed != ln:
                out.lines[i] = fixed
                out.legacy_fixed += 1

    if "strip_lone_bullets" in preprocess:  # docling list items with an empty body ("-")
        for i, ln in enumerate(out.lines):
            if re.fullmatch(r"\s*[-•·]\s*", ln):
                out.lines[i] = ""
                out.lone_bullets += 1
    return out
