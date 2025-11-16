import os
import logging
import time
import threading
import json
import httpx
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



# Load AI_NEWS_MCP_URL from environment variable with logging
AI_NEWS_MCP_URL = os.getenv("AI_NEWS_MCP_URL")

if not AI_NEWS_MCP_URL:
    logger.error("AI_NEWS_MCP_URL is NOT set in the environment.")
    raise RuntimeError("Missing AI_NEWS_MCP_URL environment variable.")
else:
    logger.info("AI_NEWS_MCP_URL is set to %s", AI_NEWS_MCP_URL)



# Load X_MCP_URL from environment variable with logging
X_MCP_URL = os.getenv("X_MCP_URL")

if not X_MCP_URL:
    logger.error("X_MCP_URL is NOT set in the environment.")
    raise RuntimeError("Missing X_MCP_URL environment variable.")
else:
    logger.info("X_MCP_URL is set to %s", X_MCP_URL)



AI_NEWS_SESSION_ID: Optional[str] = None
AI_NEWS_SESSION_LOCK = threading.Lock()
X_MCP_SESSION_ID: Optional[str] = None
X_MCP_SESSION_LOCK = threading.Lock()



# Initialize the Dedalus client
dedalus_client = Dedalus(
    api_key=DEDALUS_API_KEY,
    environment="production",  # use "development" if you're testing
)


# Initialize a DedalusRunner for MCP / tool orchestration
runner = DedalusRunner(dedalus_client)


# Initialize Firebase Admin using Avalogica service account
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "/var/secrets/avalogica/avalogica-service-account.json")

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



# Weather lane helpers
def build_mcp_weather_call(body: WeatherQuery) -> dict:
    lat = body.location.latitude
    lon = body.location.longitude
    opts = body.options or WeatherOptions()

    if body.mode == "daily_forecast":
        days = opts.days or 3
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": "get_forecast",
                "arguments": {
                    "latitude": lat,
                    "longitude": lon,
                    "days": days,
                },
            },
        }

    if body.mode == "hourly_forecast":
        hours = opts.hours or 24
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": "get_hourly_forecast",
                "arguments": {
                    "latitude": lat,
                    "longitude": lon,
                    "hours": hours,
                },
            },
        }

    if body.mode == "air_quality":
        hours = opts.hours or 24
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": "get_air_quality",
                "arguments": {
                    "latitude": lat,
                    "longitude": lon,
                    "hours": hours,
                },
            },
        }

    if body.mode == "marine_conditions":
        hours = opts.hours or 24
        return {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": "get_marine_conditions",
                "arguments": {
                    "latitude": lat,
                    "longitude": lon,
                    "hours": hours,
                },
            },
        }


    raise HTTPException(status_code=400, detail=f"Unsupported mode: {body.mode}")



# Tech update lane helper
def build_mcp_tech_update_call(body: TechUpdateQuery) -> dict:
    """
    Build a JSON-RPC tools/call payload for the get_tech_update tool
    exposed by the Avalogica AI News MCP server.
    """
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "get_tech_update",
            "arguments": {
                "topic": body.topic,
            },
        },
    }



# Tech topics listing lane helper
def build_mcp_list_tech_topics_call() -> dict:
    """
    Build a JSON-RPC tools/call payload for the list_tech_topics tool
    exposed by the Avalogica AI News MCP server.
    """
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "list_tech_topics",
            "arguments": {},
        },
    }



def build_mcp_link_x_account_call(code: str, code_verifier: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "link_x_account",
            "arguments": {
                "code": code,
                "codeVerifier": code_verifier,
                "redirectUri": "",  # Ignored by avalogica-x-mcp now
            },
        },
    }


def build_mcp_start_link_x_account_call() -> dict:
    """
    Build a JSON-RPC tools/call payload for the start_link_x_account tool
    exposed by the Avalogica X MCP server.

    This tool is expected to return an authorization URL (and any other
    metadata needed to complete the OAuth2/PKCE flow on the client side).
    """
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "start_link_x_account",
            "arguments": {},
        },
    }


def build_mcp_post_to_x_call(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "post_to_x",
            "arguments": {"text": text},
        },
    }


def build_mcp_get_recent_posts_call(limit: int = 10) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "get_recent_posts",
            "arguments": {"limit": limit},
        },
    }


def build_mcp_summarize_post_history_call(limit: int = 20) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "summarize_post_history",
            "arguments": {"limit": limit},
        },
    }



def ensure_ai_news_session() -> str:
    """Ensure there is an active MCP session and return its session id.

    This sends a one-time JSON-RPC initialize request to the Avalogica AI News
    MCP server and caches the mcp-session-id response header. Subsequent calls
    reuse the cached session id.
    """
    global AI_NEWS_SESSION_ID

    with AI_NEWS_SESSION_LOCK:
        if AI_NEWS_SESSION_ID:
            return AI_NEWS_SESSION_ID

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        init_payload = {
            "jsonrpc": "2.0",
            "id": "init-1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "dedalus-bridge",
                    "version": "1.0.0",
                },
            },
        }

        try:
            resp = httpx.post(
                f"{AI_NEWS_MCP_URL}/mcp",
                json=init_payload,
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.exception("Error initializing AI News MCP session")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize AI News MCP session: {e}",
            )

        session_id = resp.headers.get("mcp-session-id")
        if not session_id:
            logger.error("AI News MCP initialize missing mcp-session-id header")
            raise HTTPException(
                status_code=500,
                detail="AI News MCP initialize failed: missing mcp-session-id header",
            )

        AI_NEWS_SESSION_ID = session_id
        logger.info("AI News MCP session initialized successfully")
        return session_id



def ensure_x_mcp_session() -> str:
    """Ensure there is an active MCP session for the Avalogica X MCP server."""
    global X_MCP_SESSION_ID

    with X_MCP_SESSION_LOCK:
        if X_MCP_SESSION_ID:
            return X_MCP_SESSION_ID

        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }

        init_payload = {
            "jsonrpc": "2.0",
            "id": "init-x-1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "dedalus-bridge",
                    "version": "1.0.0",
                },
            },
        }

        try:
            resp = httpx.post(
                f"{X_MCP_URL}/mcp",
                json=init_payload,
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.exception("Error initializing Avalogica X MCP session")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize Avalogica X MCP session: {e}",
            )

        session_id = resp.headers.get("mcp-session-id")
        if not session_id:
            logger.error("Avalogica X MCP initialize missing mcp-session-id header")
            raise HTTPException(
                status_code=500,
                detail="Avalogica X MCP initialize failed: missing mcp-session-id header",
            )

        X_MCP_SESSION_ID = session_id
        logger.info("Avalogica X MCP session initialized successfully")
        return session_id



def call_ai_news_mcp(mcp_payload: dict) -> dict:
    """
    Calls the Avalogica AI News MCP server hosted on Cloud Run.

    Ensures the MCP server is initialized (via ensure_ai_news_session) and then
    sends a single JSON-RPC tools/call request in one HTTP POST, including the
    Mcp-Session-Id header so the server recognizes the active session.
    """
    session_id = ensure_ai_news_session()

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Session-Id": session_id,
    }

    try:
        resp = httpx.post(
            f"{AI_NEWS_MCP_URL}/mcp",
            json=mcp_payload,
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "").lower()

        # If the MCP server is using Server-Sent Events, extract the JSON from data: lines
        if "text/event-stream" in content_type:
            raw = resp.text
            parsed = None
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    json_str = line[len("data:"):].strip()
                    if json_str:
                        # Keep the last valid data: block as the final JSON-RPC message
                        parsed = json.loads(json_str)
            if parsed is None:
                logger.error("AI News MCP SSE response did not contain any data: lines")
                raise HTTPException(
                    status_code=500,
                    detail="AI News MCP error: invalid SSE payload (no data lines)",
                )
            data = parsed
        else:
            # Non-SSE: assume plain JSON
            try:
                data = resp.json()
            except ValueError as e:
                logger.exception("Failed to parse AI News MCP JSON response")
                raise HTTPException(
                    status_code=500,
                    detail=f"Avalogica AI News MCP error: {e}",
                )

        # Prefer a single JSON-RPC response object, but handle a batch defensively
        if isinstance(data, dict):
            if "error" in data:
                logger.error("AI News MCP tools/call error: %s", data["error"])
                raise HTTPException(
                    status_code=500,
                    detail=f"AI News MCP tools/call error: {data['error']}",
                )
            if "result" in data:
                return data["result"]
            return data

        if isinstance(data, list):
            target_id = mcp_payload.get("id")
            if target_id is not None:
                for item in data:
                    if isinstance(item, dict) and item.get("id") == target_id:
                        if "error" in item:
                            logger.error("AI News MCP tools/call error: %s", item["error"])
                            raise HTTPException(
                                status_code=500,
                                detail=f"AI News MCP tools/call error: {item['error']}",
                            )
                        if "result" in item:
                            return item["result"]
                        return item
            # Fallback: return the last element in the batch
            last = data[-1]
            if isinstance(last, dict) and "error" in last:
                logger.error("AI News MCP tools/call error: %s", last["error"])
                raise HTTPException(
                    status_code=500,
                    detail=f"AI News MCP tools/call error: {last['error']}",
                )
            if isinstance(last, dict) and "result" in last:
                return last["result"]
            return last

        # If the response is neither dict nor list, just return it as-is
        return data

    except HTTPException:
        # Already wrapped
        raise
    except Exception as e:
        logger.exception("Error while calling Avalogica AI News MCP")
        raise HTTPException(
            status_code=500,
            detail=f"Avalogica AI News MCP error: {e}",
        )



def call_x_mcp(mcp_payload: dict, user: AuthedUser) -> dict:
    """
    Calls the Avalogica X MCP server hosted on Cloud Run.
    Injects the authenticated Dedalus user ID as X-Dedalus-User-Id.
    """
    session_id = ensure_x_mcp_session()

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Session-Id": session_id,
        "X-Dedalus-User-Id": user.uid,
    }

    try:
        resp = httpx.post(
            f"{X_MCP_URL}/mcp",
            json=mcp_payload,
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "").lower()

        if "text/event-stream" in content_type:
            raw = resp.text
            parsed = None
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    json_str = line[len("data:"):].strip()
                    if json_str:
                        parsed = json.loads(json_str)
            if parsed is None:
                logger.error("X MCP SSE response missing data: lines")
                raise HTTPException(
                    status_code=500,
                    detail="Avalogica X MCP error: invalid SSE payload (no data lines)",
                )
            data = parsed
        else:
            try:
                data = resp.json()
            except ValueError as e:
                logger.exception("Failed to parse X MCP JSON response")
                raise HTTPException(
                    status_code=500,
                    detail=f"Avalogica X MCP error: {e}",
                )

        if isinstance(data, dict):
            if "error" in data:
                logger.error("X MCP tools/call error: %s", data["error"])
                raise HTTPException(
                    status_code=500,
                    detail=f"Avalogica X MCP tools/call error: {data['error']}",
                )
            if "result" in data:
                return data["result"]
            return data

        if isinstance(data, list):
            target_id = mcp_payload.get("id")
            for item in data:
                if isinstance(item, dict) and item.get("id") == target_id:
                    if "error" in item:
                        logger.error("X MCP tools/call error: %s", item["error"])
                        raise HTTPException(
                            status_code=500,
                            detail=f"Avalogica X MCP tools/call error: {item['error']}",
                        )
                    if "result" in item:
                        return item["result"]
                    return item
            return data[-1]

        return data

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error while calling Avalogica X MCP")
        raise HTTPException(
            status_code=500,
            detail=f"Avalogica X MCP error: {e}",
        )



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




# Weather lane route handler
@app.post("/weather/query")
def weather_query(
    body: WeatherQuery,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Fast weather lane: routes requests to the Avalogica AI News MCP on Cloud Run.
    Requires a Firebase-authenticated user (same as /dedalus/query).
    """
    logger.info(
        f"Handling weather query for uid={user.uid}, email={user.email}, mode={body.mode}"
    )
    try:
        mcp_payload = build_mcp_weather_call(body)
        start_time = time.perf_counter()
        result = call_ai_news_mcp(mcp_payload)
        latency = time.perf_counter() - start_time
        logger.info(f"Weather MCP call latency = {latency:.3f}s")
        return result
    except HTTPException:
        # Already logged and wrapped
        raise
    except Exception as e:
        logger.exception("Unexpected error in /weather/query")
        raise HTTPException(status_code=500, detail=f"Weather query error: {e}")



# Tech update lane route handler
@app.post("/ai-news/tech-update")
def tech_update_query(
    body: TechUpdateQuery,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Tech update lane: routes requests to the Avalogica AI News MCP on Cloud Run.
    Requires a Firebase-authenticated user (same as /dedalus/query).
    """
    logger.info(
        f"Handling AI tech update query for uid={user.uid}, email={user.email}, topic={body.topic}"
    )
    try:
        mcp_payload = build_mcp_tech_update_call(body)
        start_time = time.perf_counter()
        result = call_ai_news_mcp(mcp_payload)
        latency = time.perf_counter() - start_time
        logger.info(f"Tech update MCP call latency = {latency:.3f}s")
        return result
    except HTTPException:
        # Already logged and wrapped
        raise
    except Exception as e:
        logger.exception("Unexpected error in /ai-news/tech-update")
        raise HTTPException(status_code=500, detail=f"Tech update query error: {e}")



# Tech topics listing lane route handler
@app.get("/ai-news/topics")
def list_tech_topics(
    user: AuthedUser = Depends(get_current_user),
):
    """
    Tech topics listing lane: routes requests to the Avalogica AI News MCP on Cloud Run.
    Returns the currently supported AI news topics that can be used with get_tech_update.
    Requires a Firebase-authenticated user (same as /dedalus/query).
    """
    logger.info(
        f"Handling AI tech topics query for uid={user.uid}, email={user.email}"
    )
    try:
        mcp_payload = build_mcp_list_tech_topics_call()
        start_time = time.perf_counter()
        result = call_ai_news_mcp(mcp_payload)
        latency = time.perf_counter() - start_time
        logger.info(f"Tech topics MCP call latency = {latency:.3f}s")
        return result
    except HTTPException:
        # Already logged and wrapped
        raise
    except Exception as e:
        logger.exception("Unexpected error in /ai-news/topics")
        raise HTTPException(status_code=500, detail=f"Tech topics query error: {e}")


@app.post("/x/start-link")
def start_x_link(
    user: AuthedUser = Depends(get_current_user),
):
    """
    Starts the X OAuth2 linking flow via the Avalogica X MCP server.

    This calls the start_link_x_account MCP tool, which should return an
    authorization URL (and optionally any opaque handles the client needs)
    for the client to open in a browser.
    """
    logger.info(f"Starting X link flow for uid={user.uid}")

    try:
        mcp_payload = build_mcp_start_link_x_account_call()
        start_time = time.perf_counter()
        result = call_x_mcp(mcp_payload, user)
        latency = time.perf_counter() - start_time
        logger.info(f"X MCP start-link tools/call latency = {latency:.3f}s")
        return result
    except HTTPException:
        # Already wrapped/logged
        raise
    except Exception as e:
        logger.exception("Unexpected error in /x/start-link")
        raise HTTPException(
            status_code=500,
            detail=f"X start-link error: {e}",
        )


@app.post("/x/link")
def link_x_account(
    code: str,
    code_verifier: str,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Handles X OAuth2 callback finalization via the Avalogica X MCP server.
    The actual browser callback goes directly to avalogica-x-mcp.
    This endpoint performs the MCP tool call that finishes linking.
    """
    logger.info(f"Linking X account for uid={user.uid}")

    mcp_payload = build_mcp_link_x_account_call(
        code=code,
        code_verifier=code_verifier,
    )

    result = call_x_mcp(mcp_payload, user)
    return result




if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080))
    )



