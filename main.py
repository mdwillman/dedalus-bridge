import os
import logging
import time
import json

from models import (
    QueryRequest,
    Location,
    WeatherOptions,
    WeatherQuery,
    TechUpdateQuery,
    AuthedUser,
    SummarizeResponse,
    PostToXRequest,
    EmergentSignalsQuery,
    EdgeCommunitiesQuery,
    SimilarPagesQuery,
    FetchPageContentsQuery,
)

from deps import (
    get_current_user,
    ensure_ai_news_session,
    ensure_x_mcp_session,
    call_ai_news_mcp,
    call_x_mcp,
    call_consumer_needs_mcp,
)

from fastapi import FastAPI, HTTPException, Depends, Query
from dedalus_labs import Dedalus, DedalusRunner

from dotenv import load_dotenv
load_dotenv()


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


# Load CONSUMER_NEEDS_MCP_URL from environment variable with logging
CONSUMER_NEEDS_MCP_URL = os.getenv("CONSUMER_NEEDS_MCP_URL")

if not CONSUMER_NEEDS_MCP_URL:
    logger.error("CONSUMER_NEEDS_MCP_URL is NOT set in the environment.")
    raise RuntimeError("Missing CONSUMER_NEEDS_MCP_URL environment variable.")
else:
    logger.info("CONSUMER_NEEDS_MCP_URL is set to %s", CONSUMER_NEEDS_MCP_URL)


# Initialize the Dedalus client
dedalus_client = Dedalus(
    api_key=DEDALUS_API_KEY,
    environment="production",  # use "development" if you're testing
)

# Initialize a DedalusRunner for MCP / tool orchestration
runner = DedalusRunner(dedalus_client)


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


def build_mcp_search_emergent_signals_call(body: EmergentSignalsQuery) -> dict:
    """
    Build a JSON-RPC tools/call payload for the search_emergent_signals tool
    exposed by the Avalogica Consumer Needs MCP server.
    """
    args: dict = {
        "query": body.query,
    }

    if body.num_results is not None:
        args["numResults"] = body.num_results

    if body.result_category is not None:
        # Enum -> string for JSON-RPC
        args["resultCategory"] = body.result_category.value

    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "search_emergent_signals",
            "arguments": args,
        },
    }

    logger = logging.getLogger(__name__)
    logger.info("Calling Consumer Needs MCP with payload=%s", payload)
    return payload


def build_mcp_search_edge_communities_call(body: EdgeCommunitiesQuery) -> dict:
    """
    Build a JSON-RPC tools/call payload for the search_edge_communities tool
    exposed by the Avalogica Consumer Needs MCP server.
    """
    args: dict = {
        "query": body.query,
    }

    if body.num_results is not None:
        args["numResults"] = body.num_results

    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "search_edge_communities",
            "arguments": args,
        },
    }

    logger = logging.getLogger(__name__)
    logger.info("Calling Consumer Needs MCP (edge communities) with payload=%s", payload)
    return payload


def build_mcp_find_similar_pages_call(body: SimilarPagesQuery) -> dict:
    """
    Build a JSON-RPC tools/call payload for the find_similar_pages tool
    exposed by the Avalogica Consumer Needs MCP server.
    """
    args: dict = {
        "url": body.url,
    }

    if body.num_results is not None:
        args["numResults"] = body.num_results

    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "find_similar_pages",
            "arguments": args,
        },
    }

    logger = logging.getLogger(__name__)
    logger.info("Calling Consumer Needs MCP (similar pages) with payload=%s", payload)
    return payload


def build_mcp_fetch_page_contents_call(body: FetchPageContentsQuery) -> dict:
    """
    Build a JSON-RPC tools/call payload for the fetch_page_contents tool
    exposed by the Avalogica Consumer Needs MCP server.
    """
    args: dict = {
        "url": body.url,
    }

    if body.include_subpages is not None:
        args["includeSubpages"] = body.include_subpages

    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "fetch_page_contents",
            "arguments": args,
        },
    }

    logger = logging.getLogger(__name__)
    logger.info("Calling Consumer Needs MCP (fetch page) with payload=%s", payload)
    return payload


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


def build_mcp_link_x_account_call(user_id: str, code: str, code_verifier: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "link_x_account",
            "arguments": {
                "userId": user_id,
                "code": code,
                "codeVerifier": code_verifier,
                # no redirectUri needed at all now
            },
        },
    }


def build_mcp_get_recent_posts_call(user_id: str, limit: int = 20) -> dict:
    """Builds an MCP tools/call payload for get_recent_posts."""
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "get_recent_posts",
            "arguments": {
                "userId": user_id,
                "limit": limit,
            },
        },
    }


def build_mcp_get_following_timeline_call(user_id: str, limit: int = 50) -> dict:
    """
    Builds an MCP tools/call payload for get_following_timeline.
    This fetches the authenticated user's Following timeline from Avalogica X MCP.
    """
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "get_following_timeline",
            "arguments": {
                "userId": user_id,
                "limit": limit,
            },
        },
    }


def build_mcp_summarize_post_history_call(
    user_id: str,
    limit: int = 20,
    focus: str = "all",
) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "summarize_post_history",
            "arguments": {
                "userId": user_id,
                "limit": limit,
                "focus": focus,
            },
        },
    }


def build_mcp_post_to_x_call(user_id: str, blurb: str) -> dict:
    """
    Build an MCP tools/call payload for the post_to_x tool.

    The Avalogica X MCP tool is expected to take:
      - userId: internal Dedalus user id
      - blurb:   the post content
    """
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "tools/call",
        "params": {
            "name": "post_to_x",
            "arguments": {
                "userId": user_id,
                "blurb": blurb,
            },
        },
    }


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


@app.post("/consumer-needs/emergent-signals")
def consumer_needs_emergent_signals(
    body: EmergentSignalsQuery,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Search emergent signals via the Avalogica Consumer Needs MCP on Cloud Run.
    """
    logger.info(
        "Handling consumer-needs/emergent-signals for uid=%s, query=%s",
        user.uid,
        body.query,
    )
    try:
        mcp_payload = build_mcp_search_emergent_signals_call(body)
        start_time = time.perf_counter()
        result = call_consumer_needs_mcp(mcp_payload)
        latency = time.perf_counter() - start_time
        logger.info("Consumer Needs emergent_signals MCP latency = %.3fs", latency)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in /consumer-needs/emergent-signals")
        raise HTTPException(
            status_code=500,
            detail=f"Consumer Needs emergent signals error: {e}",
        )


@app.post("/consumer-needs/edge-communities")
def consumer_needs_edge_communities(
    body: EdgeCommunitiesQuery,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Search edge communities via the Avalogica Consumer Needs MCP on Cloud Run.
    """
    logger.info(
        "Handling consumer-needs/edge-communities for uid=%s, query=%s",
        user.uid,
        body.query,
    )
    try:
        mcp_payload = build_mcp_search_edge_communities_call(body)
        start_time = time.perf_counter()
        result = call_consumer_needs_mcp(mcp_payload)
        latency = time.perf_counter() - start_time
        logger.info("Consumer Needs edge_communities MCP latency = %.3fs", latency)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in /consumer-needs/edge-communities")
        raise HTTPException(
            status_code=500,
            detail=f"Consumer Needs edge communities error: {e}",
        )


@app.post("/consumer-needs/similar-pages")
def consumer_needs_similar_pages(
    body: SimilarPagesQuery,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Find similar pages via the Avalogica Consumer Needs MCP on Cloud Run.
    """
    logger.info(
        "Handling consumer-needs/similar-pages for uid=%s, url=%s",
        user.uid,
        body.url,
    )
    try:
        mcp_payload = build_mcp_find_similar_pages_call(body)
        start_time = time.perf_counter()
        result = call_consumer_needs_mcp(mcp_payload)
        latency = time.perf_counter() - start_time
        logger.info("Consumer Needs find_similar_pages MCP latency = %.3fs", latency)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in /consumer-needs/similar-pages")
        raise HTTPException(
            status_code=500,
            detail=f"Consumer Needs similar pages error: {e}",
        )


@app.post("/consumer-needs/page-contents")
def consumer_needs_page_contents(
    body: FetchPageContentsQuery,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Fetch page (and optional subpage) contents via the Avalogica Consumer Needs MCP.
    """
    logger.info(
        "Handling consumer-needs/page-contents for uid=%s, url=%s, include_subpages=%s",
        user.uid,
        body.url,
        body.include_subpages,
    )
    try:
        mcp_payload = build_mcp_fetch_page_contents_call(body)
        start_time = time.perf_counter()
        result = call_consumer_needs_mcp(mcp_payload)
        latency = time.perf_counter() - start_time
        logger.info("Consumer Needs fetch_page_contents MCP latency = %.3fs", latency)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in /consumer-needs/page-contents")
        raise HTTPException(
            status_code=500,
            detail=f"Consumer Needs page contents error: {e}",
        )


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
        user_id=user.uid,          # <-- REQUIRED
        code=code,
        code_verifier=code_verifier,
    )

    result = call_x_mcp(mcp_payload, user)
    return result


@app.get("/x/recent")
def get_x_recent_posts(
    limit: int = 20,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Fetch recent X posts for the currently authenticated user.
    Proxies to Avalogica X MCP get_recent_posts.
    """
    try:
        # Optional: clamp limit here as well (defensive)
        safe_limit = max(5, min(limit, 100))

        mcp_payload = build_mcp_get_recent_posts_call(
            user_id=user.uid,
            limit=safe_limit,
        )

        logger.info(
            "Calling X MCP get_recent_posts for user %s (limit=%d)",
            user.uid,
            safe_limit,
        )

        result = call_x_mcp(mcp_payload, user)

        return result

    except HTTPException:
        # let explicit HTTP errors through
        raise
    except Exception as e:
        logger.exception("Unexpected error in /x/recent")
        raise HTTPException(
            status_code=500,
            detail=f"X recent posts error: {e}",
        )


@app.get("/x/following")
def get_x_following_timeline(
    limit: int = 50,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Fetch the authenticated user's X Following timeline.
    Proxies to Avalogica X MCP get_following_timeline.
    """
    try:
        # Clamp limit defensively to match MCP / X constraints
        safe_limit = max(5, min(limit, 100))

        mcp_payload = build_mcp_get_following_timeline_call(
            user_id=user.uid,
            limit=safe_limit,
        )

        logger.info(
            "Calling X MCP get_following_timeline for user %s (limit=%d)",
            user.uid,
            safe_limit,
        )

        result = call_x_mcp(mcp_payload, user)

        return result

    except HTTPException:
        # propagate explicit HTTP errors
        raise
    except Exception as e:
        logger.exception("Unexpected error in /x/following")
        raise HTTPException(
            status_code=500,
            detail=f"X Following timeline error: {e}",
        )


@app.get("/x/summarize", response_model=SummarizeResponse)
def summarize_post_history(
    limit: int = Query(20, ge=1, le=100),
    user: AuthedUser = Depends(get_current_user),
):
    safe_limit = max(10, min(limit, 100))
    logger.info(f"[summarize_post_history] Request received (limit={safe_limit})")

    try:
        mcp_payload = build_mcp_summarize_post_history_call(
            user_id=user.uid,
            limit=safe_limit,
            focus="all",  # or expose as a query param later
        )

        result = call_x_mcp(mcp_payload, user)

        # result["content"][0]["text"] is the JSON string we returned from the MCP tool
        content = result.get("content", [])
        text = ""
        if content and isinstance(content, list) and content[0].get("type") == "text":
            text = content[0].get("text", "")

        return SummarizeResponse(
            limit=safe_limit,
            post_count=safe_limit,  # you can parse real count from `text` later
            summary=text or "No summary returned from MCP.",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[summarize_post_history] UNEXPECTED ERROR: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Unexpected server error during post summarization.",
        )


@app.post("/x/post")
def post_to_x(
    body: PostToXRequest,
    user: AuthedUser = Depends(get_current_user),
):
    """
    Post a new update to X on behalf of the authenticated user.
    Proxies to Avalogica X MCP post_to_x.
    """
    text = (body.text or "").strip()

    if not text:
        raise HTTPException(status_code=400, detail="Text must not be empty.")

    # Optional: clamp length locally; X MCP / X API will also enforce limits.
    if len(text) > 1000:
        raise HTTPException(
            status_code=400,
            detail="Text is too long; please shorten your post.",
        )

    try:
        mcp_payload = build_mcp_post_to_x_call(
            user_id=user.uid,
            blurb=text,
        )

        logger.info(
            "Calling X MCP post_to_x for user %s (len(text)=%d)",
            user.uid,
            len(text),
        )

        result = call_x_mcp(mcp_payload, user)
        return result

    except HTTPException:
        # pass through wrapped MCP / auth errors
        raise
    except Exception as e:
        logger.exception("Unexpected error in /x/post")
        raise HTTPException(
            status_code=500,
            detail=f"X post error: {e}",
        )
    

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8080))
    )
