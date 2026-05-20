#!/usr/bin/env python3
"""Quick test to verify all imports and basic functionality"""

import sys

def test_imports():
    """Test that all modules can be imported"""
    print("Testing imports...")
    try:
        import database
        print("  ✓ database")
        import models
        print("  ✓ models")
        import scraper
        print("  ✓ scraper")
        import scheduler
        print("  ✓ scheduler")
        from api import companies, profiles, jobs, pipeline
        print("  ✓ api.companies")
        print("  ✓ api.profiles")
        print("  ✓ api.jobs")
        print("  ✓ api.pipeline")
        import main
        print("  ✓ main")
        return True
    except ImportError as e:
        print(f"  ✗ Import error: {e}")
        return False

def test_database():
    """Test database initialization"""
    print("\nTesting database...")
    try:
        from database import init_db, SessionLocal
        from models import Company, KeywordProfile, Job, PipelineJob

        # Create tables
        init_db()
        print("  ✓ Database initialized")

        # Test session
        db = SessionLocal()
        db.close()
        print("  ✓ Session creation works")
        return True
    except Exception as e:
        print(f"  ✗ Database error: {e}")
        return False

def test_models():
    """Test model functionality"""
    print("\nTesting models...")
    try:
        from models import Company, KeywordProfile

        # Test Company
        company = Company(name="Test Corp", career_url="https://example.com")
        assert company.name == "Test Corp"
        print("  ✓ Company model works")

        # Test KeywordProfile
        profile = KeywordProfile(name="Test Profile")
        profile.set_keywords(["keyword1", "keyword2"])
        keywords = profile.get_keywords()
        assert "keyword1" in keywords
        print("  ✓ KeywordProfile model works")

        return True
    except Exception as e:
        print(f"  ✗ Model error: {e}")
        return False

def main():
    """Run all tests"""
    print("=" * 50)
    print("Career Aggregator - Quick Test")
    print("=" * 50)

    tests = [
        ("Imports", test_imports),
        ("Database", test_database),
        ("Models", test_models),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"Unexpected error in {name}: {e}")
            results.append((name, False))

    print("\n" + "=" * 50)
    print("Test Results:")
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")

    all_passed = all(r for _, r in results)

    if all_passed:
        print("\n✅ All tests passed!")
        print("\nYou can now run:")
        print("  1. python setup.py  (to add sample data)")
        print("  2. python main.py   (to start the server)")
        return 0
    else:
        print("\n❌ Some tests failed. Please check errors above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
