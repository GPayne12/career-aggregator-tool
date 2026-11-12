import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import logging
from contextlib import asynccontextmanager
from database import init_db
from api import companies, discovery, profiles, jobs, pipeline, scrape, settings

# Set up logging
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Initialize database
init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Career Aggregator...")
    yield
    logger.info("Shutting down Career Aggregator...")


# Create FastAPI app
app = FastAPI(title="Career Aggregator", version="0.1.0", lifespan=lifespan)

# Create static directory if it doesn't exist
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Include routers
app.include_router(companies.router)
app.include_router(discovery.router)
app.include_router(profiles.router)
app.include_router(jobs.router)
app.include_router(pipeline.router)
app.include_router(scrape.router)
app.include_router(settings.router)

# Mount static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/survey", response_class=HTMLResponse)
def survey_page(request: Request):
    return templates.TemplateResponse("survey.html", {"request": request})


@app.get("/search")
def search_page_moved():
    """The Scrape page became Survey; old links land there."""
    return RedirectResponse("/survey", status_code=308)


@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(request: Request):
    return templates.TemplateResponse("pipeline.html", {"request": request})


@app.get("/companies/manage", response_class=HTMLResponse)
def companies_manage_page(request: Request):
    return templates.TemplateResponse("companies_manage.html", {"request": request})


@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    return templates.TemplateResponse("profile.html", {"request": request})


@app.get("/profiles/manage")
def profiles_page_moved():
    """Keyword profiles became the one Profile; old links land there."""
    return RedirectResponse("/profile", status_code=308)


@app.get("/health")
def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
