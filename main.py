import os
import logging
import time
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from dedalus_labs import Dedalus, DedalusRunner
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials

# Initialize FastAPI app
app = FastAPI(
    title="Dedalus Bridge Service",
    description="A lightweight bridge for routing app requests to the Dedalus API via FastAPI and Cloud Run.",
    version="1.0.0"
)

# Configure logger
logger = logging.getLogger("dedalus-bridge")

# Load API key from environment variable with logging
DEDALUS_API_KEY = os.getenv("DEDALUS_API_KEY")

if not DEDALUS_API_KEY:
    logger.error("DEDALUS_API_KEY is NOT set in the environment.")
    raise RuntimeError("Missing DEDALUS_API_KEY environment variable.")
else:
    # Don't log the key itself, just that it exists and its length
    logger.info("DEDALUS_API_KEY is set; length=%d", len(DEDALUS_API_KEY))

# Initialize the Dedalus client
dedalus_client = Dedalus(
    api_key=DEDALUS_API_KEY,
    environment="production",  # use "development" if you're testing
)

# Initialize a DedalusRunner for MCP / tool orchestration
runner = DedalusRunner(dedalus_client)

# Initialize Firebase Admin using Avalogica service account
FIREBASE_CRED_PATH = "/var/secrets/avalogica/avalogica-service-account.json"

try:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin initialized successfully using Avalogica service account.")
except Exception as e:
    logger.error(f"Failed to initialize Firebase Admin: {e}")
    raise

# Define request model
class QueryRequest(BaseModel):
    prompt: str
    model: str = "openai/gpt-5-mini"  # default model, can be overridden
    mcp_servers: Optional[List[str]] = None

# AuthedUser model and authentication dependency
class AuthedUser(BaseModel):
    uid: str
    email: Optional[str] = None

async def get_current_user(authorization: str = Header(None)) -> AuthedUser:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    id_token = authorization.split(" ", 1)[1].strip()

    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        logger.warning(f"Failed to verify Firebase ID token: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    uid = decoded.get("uid")
    email = decoded.get("email")

    if not uid:
        raise HTTPException(status_code=401, detail="Token missing uid claim")

    return AuthedUser(uid=uid, email=email)

@app.post("/dedalus/query")
def query_dedalus(
    req: QueryRequest,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Accepts a text prompt and relays it to the Dedalus API.
    Returns the model's completion or raises an HTTPException on failure.
    """
    logger.info(
        f"Handling Dedalus query for uid={user.uid}, email={user.email}, "
        f"model={req.model}, mcp_servers={req.mcp_servers}"
    )
    try:
        # If MCP servers are provided, use DedalusRunner to orchestrate tools
        if req.mcp_servers:
            logger.info(f"Invoking DedalusRunner with MCP servers: {req.mcp_servers}")
            start_time = time.perf_counter()
            result = runner.run(
                input=req.prompt,
                model=req.model,
                mcp_servers=req.mcp_servers,
                stream=False,
            )
            dedalus_latency = time.perf_counter() - start_time
            logger.info(f"DedalusRunner.run latency = {dedalus_latency:.3f}s")
            return {"response": result.final_output}

        # Otherwise, fall back to a plain chat completion
        start_time = time.perf_counter()
        completion = dedalus_client.chat.completions.create(
            messages=[
                {"role": "user", "content": req.prompt}
            ],
            model=req.model,
        )
        dedalus_latency = time.perf_counter() - start_time
        logger.info(f"chat.completions.create latency = {dedalus_latency:.3f}s")

        # Handle both object-style and dict-style responses
        choice0 = completion.choices[0]

        # Get the message object or dict
        message = getattr(choice0, "message", None)
        if message is None and isinstance(choice0, dict):
            message = choice0.get("message")

        content = None
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)

        if not content:
            logger.error(f"Unexpected completion structure from Dedalus: {completion}")
            raise RuntimeError("Unexpected completion format from Dedalus")

        return {"response": content}

    except Exception as e:
        logger.exception("Error while calling Dedalus")
        raise HTTPException(status_code=500, detail=f"Dedalus API error: {e}")

# Root route (simple health check)
@app.get("/")
def root():
    return {"status": "ok", "message": "Dedalus Bridge API is running"}

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080))
    )