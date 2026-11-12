"""
Initialize the SQLite database.
Run this once before starting the app.
    python init_db.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database.db import Base, engine
from app.models.job_model import Job  # registers the model

def init_database():
    Base.metadata.create_all(bind=engine)
    print("✅ Database initialized: data/jobs/jobs.db")

if __name__ == "__main__":
    init_database()
