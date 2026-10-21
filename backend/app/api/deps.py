from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()
