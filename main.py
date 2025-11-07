import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dedalus_labs import Dedalus

# Initialize FastAPI app
app = FastAPI(
    title="Dedalus Bridge Service",
    description="A lightweight bridge for routing app requests to the Dedalus API via FastAPI and Cloud Run.",
    version="1.0.0"
)

# Load API key from environment variable
DEDALUS_API_KEY = os.getenv("DEDALUS_API_KEY")
if not DEDALUS_API_KEY:
    raise RuntimeError("Missing DEDALUS_API_KEY environment variable.")

# Initialize the Dedalus client
dedalus_client = Dedalus(
    api_key=DEDALUS_API_KEY,
    environment="production",  # use "development" if you're testing
)

# Define request model
class QueryRequest(BaseModel):
    prompt: str
    model: str = "openai/gpt-5"  # default model, can be overridden

# Define route
@app.post("/dedalus/query")
def query_dedalus(req: QueryRequest):
    """
    Accepts a text prompt and relays it to the Dedalus API.
    Returns the model's completion or raises an HTTPException on failure.
    """
    try:
        response = dedalus_client.chat.completions.create(
            messages=[
                {"role": "user", "content": req.prompt}
            ],
            model=req.model,
        )
        # Extract and return content
        message = response.choices[0].message.content
        return {"response": message}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Dedalus API error: {e}")

# Root route (simple health check)
@app.get("/")
def root():
    return {"status": "ok", "message": "Dedalus Bridge API is running"}