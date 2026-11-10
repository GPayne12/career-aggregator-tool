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
            "ALTER TABLE jobs ADD COLUMN description TEXT",
            "ALTER TABLE pipeline_jobs ADD COLUMN posting_text TEXT",
            "ALTER TABLE pipeline_jobs ADD COLUMN posting_state VARCHAR(20)",
            "ALTER TABLE pipeline_jobs ADD COLUMN work_json TEXT",
            "ALTER TABLE pipeline_jobs ADD COLUMN checklist_json TEXT",
            "CREATE TABLE IF NOT EXISTS settings (key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL)",
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('exclude_terms', '[\"intern\", \"internship\", \"co-op\", \"co op\"]')",
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
