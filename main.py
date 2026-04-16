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
EXTERNAL_URL = os.environ.get("EXTERNAL_URL", "https://mcp.bes-systemhaus.de")
logger.info(f"Proxy bereit – {len(USER_MAP)} User geladen | Upstream: {UPSTREAM}")

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(request: Request, path: str):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    bearer_token = auth_header.removeprefix("Bearer ").strip()
    user_info = USER_MAP.get(bearer_token)

    if not user_info:
        raise HTTPException(status_code=403, detail="Unknown token")

    username, confluence_pat = user_info
    logger.info(f"✓ {username} | {request.method} /{path}")

    headers = dict(request.headers)
    headers["Authorization"] = f"Bearer {confluence_pat}"
    headers.pop("host", None)

    body = await request.body()

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.request(
            method=request.method,
            url=f"{UPSTREAM}/{path}",
            headers=headers,
            content=body,
            params=request.query_params,
        )

    # SSE-Antwort: interne URLs ersetzen
    content = resp.content
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type or path == "sse":
        content = content.replace(
            UPSTREAM.encode(),
            EXTERNAL_URL.encode()
        )

    return Response(
        content=content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )
