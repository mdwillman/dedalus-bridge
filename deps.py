import json
import threading
import os
import httpx
import logging
from typing import Optional

from fastapi import Header, HTTPException
from models import AuthedUser  # and any other models the deps use

from firebase_admin import auth as firebase_auth, credentials
import firebase_admin

# Environment / config
AI_NEWS_MCP_URL = os.getenv("AI_NEWS_MCP_URL")
X_MCP_URL = os.getenv("X_MCP_URL")

logger = logging.getLogger("dedalus-bridge")

AI_NEWS_SESSION_ID: Optional[str] = None
AI_NEWS_SESSION_LOCK = threading.Lock()
X_MCP_SESSION_ID: Optional[str] = None
X_MCP_SESSION_LOCK = threading.Lock()

# Initialize Firebase Admin using Avalogica service account
FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "/var/secrets/avalogica/avalogica-service-account.json")

try:
    cred = credentials.Certificate(FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred)
    logger.info("Firebase Admin initialized successfully using Avalogica service account.")
except Exception as e:
    logger.error(f"Failed to initialize Firebase Admin: {e}")
    raise


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

