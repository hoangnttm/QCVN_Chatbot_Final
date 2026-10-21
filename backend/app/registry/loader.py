"""Load and verify the Document Registry (data/registry/documents.yaml)."""

import hashlib
import unicodedata
from pathlib import Path

import yaml

from app.core.config import Settings, get_settings
from app.registry.schema import Document, Registry


class RegistryError(RuntimeError):
    pass


def load_registry(path: Path | None = None, settings: Settings | None = None) -> Registry:
    settings = settings or get_settings()
    path = path or settings.registry_file
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Registry.model_validate(raw)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_source_path(file_name: str, settings: Settings | None = None) -> Path | None:
    """Locate a source file regardless of the Unicode normalization form of its on-disk name.

    Windows (and files copied from macOS) may store Vietnamese names in NFD while the registry
    is written in NFC; both must resolve to the same file.
    """
    settings = settings or get_settings()
    for form in ("NFC", "NFD"):
        candidate = settings.sources_dir / unicodedata.normalize(form, file_name)
        if candidate.exists():
            return candidate
    return None


def verify_sources(registry: Registry, settings: Settings | None = None) -> list[str]:
    """Return a list of problems (empty = all source files present, hashes and sizes match)."""
    settings = settings or get_settings()
    problems: list[str] = []
    for doc in registry.documents:
        src = doc.source
        if src is None:
            continue
        fpath = resolve_source_path(src.file, settings)
        if fpath is None:
            problems.append(f"{doc.document_id}: missing file {settings.sources_dir / src.file}")
            continue
        size = fpath.stat().st_size
        if size != src.size_bytes:
            problems.append(f"{doc.document_id}: size {size} != registry {src.size_bytes}")
        digest = sha256_of(fpath)
        if digest != src.sha256:
            problems.append(
                f"{doc.document_id}: sha256 mismatch ({digest[:12]}… != {src.sha256[:12]}…)"
            )
    return problems


def verify_referenced_files(registry: Registry, settings: Settings | None = None) -> list[str]:
    """Grammar and scope files declared in the registry must exist under data/."""
    settings = settings or get_settings()
    problems: list[str] = []
    for doc in registry.documents:
        for rel in (doc.grammar, doc.scope_file):
            if rel and not (settings.data_dir / rel).exists():
                problems.append(f"{doc.document_id}: referenced file data/{rel} not found")
    return problems


def documents_with_source(registry: Registry) -> list[Document]:
    return [d for d in registry.documents if d.source is not None]
