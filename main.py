import logging
import os
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-proxy")

app = FastAPI()

EXTERNAL_URL = os.getenv("EXTERNAL_URL", "http://localhost:8080")

# -------------------------------------------------------
# USER_* Env-Variablen einlesen
# Format: USER_NAME=bearer_token:upstream_url
# Beispiel: USER_SCHERMER=abc123:http://mcp-atlassian-schermer:9000
# -------------------------------------------------------
user_map: dict[str, str] = {}
for key, value in os.environ.items():
    if key.startswith("USER_"):
        parts = value.split(":", 1)
        if len(parts) == 2:
            bearer, upstream = parts
            user_map[bearer] = upstream.rstrip("/")
            logger.info(f"Registered {key} → {upstream}")
        else:
            logger.warning(f"Skipping {key}: expected format bearer:upstream_url")


def get_upstream(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[len("Bearer "):]
    return user_map.get(token)


def build_headers(request: Request) -> dict:
    headers = dict(request.headers)
    headers.pop("host", None)
    return headers


# -------------------------------------------------------
# SSE Route — echtes Streaming + URL-Rewriting
# -------------------------------------------------------
@app.get("/sse")
async def sse_proxy(request: Request):
    upstream = get_upstream(request)
    if not upstream:
        return Response(status_code=401, content="Unauthorized")

    headers = build_headers(request)
    logger.info(f"SSE connect → {upstream}/sse")

    async def stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{upstream}/sse", headers=headers) as resp:
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        rewritten = chunk.replace(
                            upstream.encode(), EXTERNAL_URL.encode()
                        )
                        logger.debug(f"SSE chunk: {rewritten[:200]}")
                        yield rewritten

    return StreamingResponse(stream(), media_type="text/event-stream")


# -------------------------------------------------------
# Alle anderen Routen — normales Forwarding
# -------------------------------------------------------
@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
)
async def proxy(request: Request, path: str):
    upstream = get_upstream(request)
    if not upstream:
        return Response(status_code=401, content="Unauthorized")

    headers = build_headers(request)
    body = await request.body()
    url = f"{upstream}/{path}"

    logger.info(f"{request.method} /{path} → {url}")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
        logger.info(f"Response: {resp.status_code}")
        logger.info(f"Response body: {resp.content[:500]}")

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )
