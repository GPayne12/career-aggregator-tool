#!/usr/bin/env python3
"""
Quick setup script to initialize the database and add sample data.
Run with: python setup.py
"""

from database import SessionLocal, init_db
from models import Company, KeywordProfile

def setup():
    """Initialize database and add sample data"""
    print("Initializing database...")
    init_db()

    db = SessionLocal()

    # Add sample companies
    sample_companies = [
        {"name": "Capital One", "career_url": "https://capitalone.wd1.myworkdayjobs.com"},
        {"name": "Transamerica", "career_url": "https://www.transamerica.com/careers"},
        {"name": "HMH", "career_url": "https://www.hmhco.com/about/careers"},
    ]

    print("Adding sample companies...")
    for company_data in sample_companies:
        existing = db.query(Company).filter(Company.name == company_data["name"]).first()
        if not existing:
            company = Company(**company_data)
            db.add(company)
            print(f"  ✓ Added {company_data['name']}")
        else:
            print(f"  → {company_data['name']} already exists")

    # Add sample keyword profiles
    sample_profiles = [
        {
            "name": "L&D",
            "keywords": ["instructional designer", "learning designer", "learning experience"]
        },
        {
            "name": "Product",
            "keywords": ["product manager", "product designer", "product specialist"]
        },
        {
            "name": "Tech Writing",
            "keywords": ["technical writer", "documentation", "technical content"]
        },
    ]

    print("\nAdding sample keyword profiles...")
    for profile_data in sample_profiles:
        existing = db.query(KeywordProfile).filter(KeywordProfile.name == profile_data["name"]).first()
        if not existing:
            profile = KeywordProfile(name=profile_data["name"])
            profile.set_keywords(profile_data["keywords"])
            db.add(profile)
            print(f"  ✓ Added profile '{profile_data['name']}'")
        else:
            print(f"  → Profile '{profile_data['name']}' already exists")

    db.commit()
    db.close()

    print("\n✅ Setup complete!")
    print("\nYou can now run: python main.py")
    print("Then visit: http://localhost:8000")

if __name__ == "__main__":
    setup()
