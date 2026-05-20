# Quick Start Guide

## Installation & Setup (5 minutes)

### Step 1: Install Python dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Initialize database and add sample companies
```bash
python3 setup.py
```

This will:
- Create SQLite database (career_aggregator.db)
- Add 3 sample companies (Capital One, Transamerica, HMH)
- Create 3 sample keyword profiles (L&D, Product, Tech Writing)

### Step 3: Start the application
```bash
python3 main.py
```

You should see:
```
INFO:     Started server process [pid]
INFO:     Application startup complete
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Starting scheduler...
```

### Step 4: Open in browser
Visit **http://localhost:8000**

## First-Time Workflow

### 1. Add Your Companies (5 min)
- Click **Companies** in the navigation
- Add the 100+ companies from your spreadsheet
- For each company, enter:
  - Company name
  - Career page URL (e.g., https://capitalone.wd1.myworkdayjobs.com)
  - Optional notes

**Tip:** You can come back and edit/delete companies anytime

### 2. Create Keyword Profiles (5 min)
- Click **Profiles** in the navigation
- Create profiles for your searches (examples):
  - **L&D**: instructional designer, learning designer, learning experience designer, training specialist
  - **Product**: product manager, product designer, product specialist, UX designer
  - **Tech Writing**: technical writer, documentation, content strategist, technical content
  - **Leadership**: director, manager, lead, head of

**Tip:** You can create as many profiles as you want and update them anytime

### 3. Start Searching (daily)
- Click **Search** in the navigation
- Select a keyword profile (e.g., "L&D")
- Optional: Filter by location type (Remote/On-site/Hybrid) or location text
- Click **Search**
- Results show: Company | Title | Location | Salary | Posted Date | Action
- Click **Save** to add interesting jobs to your pipeline

**Tip:** Results only show jobs from the last 14 days

### 4. Track Your Applications
- Click **Pipeline** in the navigation
- View jobs grouped by stage:
  - **Saved**: Jobs you've bookmarked
  - **Applied**: Jobs you've applied to
  - **Rejected**: Didn't move forward
  - **Interviewing**: In interview process
  - **Offer**: Received an offer
- Move jobs between stages by clicking the "Move to..." dropdown
- Click job title to view full listing on company website
- Add notes for each job (interview feedback, next steps, etc.)

## How It Works

### Automatic Job Scraping
The app runs a daily scraper (default: 6 AM) that:
- Fetches job listings from each company's career page
- Extracts title, location, salary, and posting date
- Stores in local database
- Auto-removes jobs older than 14 days

### Search & Filter
When you search:
- Keyword profile keywords are matched against job titles
- Location filters narrow by type (remote/on-site/hybrid) or text
- Results are sorted by newest first
- Only jobs from last 14 days are shown

### Pipeline Tracking
Jobs you save are added to your pipeline and can be moved through stages:
- **Saved** → Found a job you're interested in
- **Applied** → Submitted application
- **Rejected** → Didn't move forward
- **Interviewing** → In active interview process
- **Offer** → Received an offer

## Configuration

### Customize Scrape Time
Edit `.env`:
```
SCRAPE_SCHEDULE_HOUR=6      # 0-23 (6 = 6 AM)
SCRAPE_SCHEDULE_MINUTE=0    # 0-59
```

### Customize Database Location
Edit `.env`:
```
DATABASE_URL=sqlite:///./career_aggregator.db
```

## Keyboard Shortcuts & Tips

- **Search page**: Enter keyword profile, select location, click "Search"
- **Pipeline page**: Click job title → opens company listing in new tab
- **Companies**: Can add/edit/delete anytime
- **Profiles**: Update keywords anytime to refine searches

## Troubleshooting

### No jobs appearing in search?
- Make sure you've added companies to track
- Check companies have valid career URLs
- Wait for first automated scrape (runs at scheduled time) or:
  ```python
  # Manually trigger scrape from Python
  from scheduler import scrape_all_companies
  scrape_all_companies()
  ```

### Scheduler not running?
- Check log output in terminal
- Verify APScheduler is installed: `pip list | grep apscheduler`
- Restart app: `Ctrl+C` then `python3 main.py`

### Can't connect to database?
- Ensure SQLite3 is installed: `python3 -c "import sqlite3; print('OK')"`
- Check file permissions on career_aggregator.db
- Delete db file and reinitialize: `rm career_aggregator.db && python3 setup.py`

## Data Backup

Your data is stored in `career_aggregator.db`. To backup:
```bash
cp career_aggregator.db career_aggregator.db.backup
```

## Next Steps

1. **Load your companies**: Go to Companies page, add your 100+ companies from spreadsheet
2. **Set up profiles**: Go to Profiles page, create search profiles for your target roles
3. **First search**: Try a search to see results
4. **Check tomorrow**: Automated scraper will run tomorrow morning
5. **Track applications**: As you apply, update pipeline status

## Need More Help?

- See [README.md](README.md) for architecture details
- Check plan at: `(local dev plan file)`
- Review application logs in terminal output

---

**Enjoy your job search! 🎯**
