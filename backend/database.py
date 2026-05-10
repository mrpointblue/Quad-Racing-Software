from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DATABASE_FILE_PATH = DATA_DIR / "quad_race.sqlite"

DATABASE_URL = f"sqlite:///{DATABASE_FILE_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
