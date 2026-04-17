from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response, StreamingResponse
import httpx
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

USER_MAP = {}
for key, value in os.environ.items():
    if key.startswith("USER_"):
        parts = value.split(":", 1)
        if len(parts) == 2:
            bearer_token, confluence_pat = parts
            username = key[5:]
            USER_MAP[bearer_token] = (username, confluence_pat)
            logger.info(f"User geladen: {username}")

UPSTREAM = os.environ.get("MCP_UPSTREAM", "http://mcp-atlassian:9000")
logger.info(f"Proxy bereit – {len(USER_MAP)} User geladen | Upstream: {UPSTREAM}")

def authenticate(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    bearer_token = auth_header.removeprefix("Bearer ").strip()
    user_info = USER_MAP.get(bearer_token)
    if not user_info:
        raise HTTPException(status_code=403, detail="Unknown token")
    return user_info

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(request: Request, path: str):
    username, confluence_pat = authenticate(request)
    logger.info(f"✓ {username} | {request.method} /{path}")

    if request.method == "GET" and path == "mcp":
        async def empty_sse():
            yield b": keepalive\n\n"
        return StreamingResponse(
            content=empty_sse(),
            status_code=200,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    headers = dict(request.headers)
    headers.pop("host", None)
    headers["Authorization"] = f"Token {confluence_pat}"

    body = await request.body()

    logger.info(f"Request body: {body[:500]}")
    logger.info(f"Forwarding Authorization: Token {confluence_pat[:10]}...")

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.request(
            method=request.method,
            url=f"{UPSTREAM}/{path}",
            headers=headers,
            content=body,
            params=request.query_params,
        )

    logger.info(f"Response status: {resp.status_code}")
    logger.info(f"Response body: {resp.content[:500]}")

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )
