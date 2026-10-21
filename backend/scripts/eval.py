"""DEPRECATED (2026-09-24): use `python scripts/evaluate.py retrieval --config C4-offline`.

Kept as a wrapper so old commands keep working; the label-prefix matching of the original
script had false positives (`TT 1` vs `TT 12`) — the new runner matches by chunk_id."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import main  # noqa: E402

if __name__ == "__main__":
    print("scripts/eval.py is deprecated -> evaluate.py retrieval --config C4-offline --split dev")
    argv = ["retrieval", "--config", "C4-offline", "--split", "dev", "--tier", "all"]
    sys.exit(main(argv))
