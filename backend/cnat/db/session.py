"""Engine/session setup. CNAT_DATABASE_URL picks the database:
  local:      sqlite:///cnat.db   (default)
  Azure SQL:  mssql+pyodbc://<user>:<pwd>@<server>.database.windows.net:1433/<db>?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from cnat.config import PROJECT_ROOT
from cnat.db.tables import Base


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CNAT_", env_file=PROJECT_ROOT / ".env", extra="ignore")
    database_url: str = f"sqlite:///{PROJECT_ROOT / 'cnat.db'}"


def make_engine(url: str | None = None) -> Engine:
    url = url or DBSettings().database_url
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def init_db(engine: Engine) -> None:
    """Create tables if missing. (Switch to Alembic migrations before the first production deploy.)"""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def unit_of_work(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
