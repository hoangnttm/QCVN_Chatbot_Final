"""DEPRECATED (2026-09-24): use `python scripts/evaluate.py qa --config C4 --split dev`.

The original script wrote into the production audit tables and derived expected numbers by
regex; the new runner keeps audit off and uses the explicit `expected_numbers` of gold v2."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import main  # noqa: E402

if __name__ == "__main__":
    print("scripts/qa_eval.py is deprecated -> evaluate.py qa --config C4 --split dev --tier B")
    sys.exit(main(["qa", "--config", "C4", "--split", "dev", "--tier", "B", "--repeats", "1"]))
