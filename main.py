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
    headers["Authorization"] = f"Token {confluence_pat}"
    headers.pop("host", None)

    body = await request.body()
    is_sse = (request.method == "GET" and path == "sse")

    if is_sse:
        async def stream_with_rewrite():
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    method="GET",
                    url=f"{UPSTREAM}/sse",
                    headers=headers,
                    params=request.query_params,
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        logger.info(f"SSE chunk: {chunk}")
                        rewritten = chunk.replace(
                            UPSTREAM.encode(),
                            EXTERNAL_URL.encode()
                        )
                        logger.info(f"SSE rewritten: {rewritten}")
                        yield rewritten

        return StreamingResponse(
            content=stream_with_rewrite(),
            status_code=200,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
    else:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.request(
                method=request.method,
                url=f"{UPSTREAM}/{path}",
                headers=headers,
                content=body,
                params=request.query_params,
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
