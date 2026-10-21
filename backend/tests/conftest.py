import pytest
from sqlalchemy import create_engine, text

from app.core.config import Settings


@pytest.fixture(scope="session")
def settings() -> Settings:
    # Defaults point at <repo>/data and <repo>/sources; no .env needed for these tests.
    return Settings(_env_file=None)


@pytest.fixture(scope="session")
def db_settings() -> Settings:
    """Throwaway `qcvn_test` database on the server named by backend/.env (DATABASE_URL);
    tests that need it are skipped when no PostgreSQL is reachable (CI without a database)."""
    from app.db import models as m

    base = Settings()  # backend/.env
    server = base.database_url.rsplit("/", 1)[0]
    try:
        admin = create_engine(f"{server}/postgres", isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(
                text("select 1 from pg_database where datname='qcvn_test'")
            ).scalar()
            if not exists:
                conn.execute(text("create database qcvn_test encoding 'UTF8' template template0"))
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL not reachable: {e}")
    test_settings = Settings(database_url=f"{server}/qcvn_test")
    eng = create_engine(test_settings.database_url)
    with eng.begin() as conn:
        conn.execute(text("create extension if not exists vector"))
        m.Base.metadata.drop_all(conn)
        m.Base.metadata.create_all(conn)
    eng.dispose()
    return test_settings
