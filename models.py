from enum import Enum
from typing import Optional, List
from pydantic import BaseModel

# Define request model
class QueryRequest(BaseModel):
    prompt: str
    model: str = "openai/gpt-4.1-mini"
    mcp_servers: Optional[List[str]] = None

# Weather lane Pydantic models
class Location(BaseModel):
    latitude: float
    longitude: float

class WeatherOptions(BaseModel):
    days: Optional[int] = None
    hours: Optional[int] = None

class WeatherQuery(BaseModel):
    mode: str  # "daily_forecast" | "hourly_forecast" | "air_quality" | "marine_conditions"
    location: Location
    options: Optional[WeatherOptions] = None

# Tech update lane Pydantic model

class TechUpdateQuery(BaseModel):
    topic: str  # e.g., "aiProductUpdates", "aiProducts", "newModels", "techResearch", "polEthicsAndSafety", "upcomingEvents"

class ExaResultCategory(str, Enum):
    company = "company"
    research_paper = "research_paper"
    news_article = "news_article"
    pdf = "pdf"
    github = "github"
    tweet = "tweet"
    personal_site = "personal_site"
    linkedin_profile = "linkedin_profile"
    financial_report = "financial_report"

# Consumer Needs lane Pydantic models
class EmergentSignalsQuery(BaseModel):
    query: str
    num_results: Optional[int] = None
    result_category: Optional[ExaResultCategory] = None

class EdgeCommunitiesQuery(BaseModel):
    query: str
    num_results: Optional[int] = None

class SimilarPagesQuery(BaseModel):
    url: str
    num_results: Optional[int] = None

class FetchPageContentsQuery(BaseModel):
    url: str
    include_subpages: Optional[bool] = False

# AuthedUser model and authentication dependency
class AuthedUser(BaseModel):
    uid: str
    email: Optional[str] = None

class SummarizeResponse(BaseModel):
    limit: int
    post_count: int
    summary: str

class PostToXRequest(BaseModel):
    text: str
