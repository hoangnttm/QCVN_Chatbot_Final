"""Delete query logs (and their answers / citations / feedback by cascade) older than
`QUERY_LOG_RETENTION_DAYS` (M12 log retention policy).

    python backend/scripts/purge_logs.py [--days N] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.db import models as m  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    settings = Settings()
    days = args.days or settings.query_log_retention_days
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    with get_sessionmaker(settings)() as session:
        n = session.execute(select(func.count()).where(m.QueryLog.created_at < cutoff)).scalar_one()
        print(f"query_logs older than {days} days (before {cutoff:%Y-%m-%d}): {n}")
        if args.dry_run or n == 0:
            return 0
        session.execute(delete(m.QueryLog).where(m.QueryLog.created_at < cutoff))
        session.commit()
        print(f"deleted {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
