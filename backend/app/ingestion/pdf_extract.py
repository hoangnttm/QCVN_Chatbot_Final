"""Step 1b: PDF -> Markdown-like lines with a page map (ADR-0009).

docling runs with OCR off (the PDFs are text-based), TableFormer ACCURATE, in page batches, and the
result is cached under data/derived/<doc>/pdf_extract/ keyed by file hash + options so the slow
conversion happens once per document version. The output keeps one docling item per line group so
the grammar-driven structure parser can consume it like a Markdown source, and every output line
knows its PDF page.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BATCH = 30
EXTRACT_VERSION = "docling-v2"  # bump when options change so caches invalidate (v2: list markers)


@dataclass
class ExtractedPdf:
    lines: list[str]
    page_of_line: list[int]  # same length as lines; 0 for blank separator lines
    n_pages: int
    n_tables: int
    failed_pages: list[int] = field(default_factory=list)
    cache_path: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _options():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.table_structure_options.do_cell_matching = True
    opts.images_scale = 1.0
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def _cache_key(file_hash: str, page_range: tuple[int, int]) -> str:
    return hashlib.sha1(
        f"{EXTRACT_VERSION}:{file_hash}:{page_range[0]}-{page_range[1]}".encode()
    ).hexdigest()[:16]


def _convert_batch(
    conv, pdf_path: Path, page_range: tuple[int, int]
) -> tuple[list[str], list[int], int, list[int]]:
    """Return (lines, page_of_line, n_tables, failed_pages) for one page batch."""
    from docling_core.types.doc import DocItemLabel

    res = conv.convert(str(pdf_path), page_range=page_range)
    doc = res.document
    lines: list[str] = []
    pages: list[int] = []
    n_tables = 0
    failed = sorted({p for p in getattr(res, "errors", []) if isinstance(p, int)})
    for item, _level in doc.iterate_items():
        prov = item.prov[0] if getattr(item, "prov", None) else None
        page = prov.page_no if prov else 0
        label = getattr(item, "label", None)
        if label == DocItemLabel.TABLE:
            n_tables += 1
            md = item.export_to_markdown(doc=doc)
            block = [ln for ln in md.split("\n") if ln.strip()]
        elif label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            continue
        else:
            text = getattr(item, "text", "") or ""
            if not text.strip():
                continue
            if label == DocItemLabel.SECTION_HEADER:
                text = "## " + text.strip()
            elif label == DocItemLabel.LIST_ITEM:
                # keep the legal numbering ("1.", "a)") — the structure grammar depends on it
                marker = (getattr(item, "marker", "") or "").strip()
                if marker and marker != "-" and not text.lstrip().startswith(marker):
                    text = f"{marker} {text.strip()}"
            block = [text.strip()]
        for ln in block:
            lines.append(ln)
            pages.append(page)
        lines.append("")
        pages.append(0)
    return lines, pages, n_tables, failed


def extract_pdf(
    pdf_path: Path,
    file_hash: str,
    cache_dir: Path,
    page_count: int,
    batch_size: int = DEFAULT_BATCH,
    page_ranges: list[tuple[int, int]] | None = None,
) -> ExtractedPdf:
    """Extract the whole PDF (or only `page_ranges`) with per-batch caching."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # Once the docling models are cached, never touch the network again: a proxy/VPN with a
    # self-signed certificate made huggingface_hub's revision check fail mid-run (2026-09-24).
    hf_cache = (
        Path.home() / ".cache" / "huggingface" / "hub" / "models--docling-project--docling-models"
    )
    if hf_cache.exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    cache_dir.mkdir(parents=True, exist_ok=True)
    ranges = page_ranges or [
        (s, min(s + batch_size - 1, page_count)) for s in range(1, page_count + 1, batch_size)
    ]
    all_lines: list[str] = []
    all_pages: list[int] = []
    n_tables = 0
    failed: list[int] = []
    conv = None
    for rng in ranges:
        cpath = cache_dir / f"{_cache_key(file_hash, rng)}.json"
        if cpath.exists():
            payload = json.loads(cpath.read_text(encoding="utf-8"))
        else:
            conv = conv or _options()
            lines, pages, nt, fl = _convert_batch(conv, pdf_path, rng)
            payload = {"range": rng, "lines": lines, "pages": pages, "n_tables": nt, "failed": fl}
            cpath.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        all_lines.extend(payload["lines"])
        all_pages.extend(payload["pages"])
        n_tables += payload["n_tables"]
        failed.extend(payload["failed"])
    # write the merged markdown for humans (not used by the parser, which reads lines + pages)
    (cache_dir / "extracted.md").write_text("\n".join(all_lines), encoding="utf-8")
    return ExtractedPdf(
        lines=all_lines,
        page_of_line=all_pages,
        n_pages=page_count,
        n_tables=n_tables,
        failed_pages=failed,
        cache_path=str(cache_dir),
    )


def _ranges(page_count: int, batch_size: int) -> list[tuple[int, int]]:
    return [(s, min(s + batch_size - 1, page_count)) for s in range(1, page_count + 1, batch_size)]


def cached_page_ranges(
    file_hash: str, cache_dir: Path, page_count: int, batch_size: int = DEFAULT_BATCH
) -> list[tuple[int, int]]:
    """Batches already converted — lets the parser be tuned while a long extraction is running."""
    return [
        r
        for r in _ranges(page_count, batch_size)
        if (cache_dir / f"{_cache_key(file_hash, r)}.json").exists()
    ]


def cached_batches_present(
    file_hash: str, cache_dir: Path, page_count: int, batch_size: int = DEFAULT_BATCH
) -> bool:
    return len(cached_page_ranges(file_hash, cache_dir, page_count, batch_size)) == len(
        _ranges(page_count, batch_size)
    )
