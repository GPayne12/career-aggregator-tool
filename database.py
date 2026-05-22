import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./career_aggregator.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables in the database"""
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE companies ADD COLUMN is_skipped BOOLEAN NOT NULL DEFAULT 0",
            "ALTER TABLE companies ADD COLUMN score_a INTEGER",
            "ALTER TABLE companies ADD COLUMN score_m INTEGER",
            "ALTER TABLE companies ADD COLUMN score_p INTEGER",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass  # Column already exists
    print(f"Database initialized at {DATABASE_URL}")


def get_db() -> Session:
    """Get a database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
