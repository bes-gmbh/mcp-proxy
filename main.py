import logging
import os
import httpx
from fastapi import FastAPI, Request, Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-proxy")

app = FastAPI()

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
    excluded = {"host", "transfer-encoding", "connection", "keep-alive", "te", "trailers", "upgrade"}
    return {k: v for k, v in request.headers.items() if k.lower() not in excluded}


HOP_BY_HOP = {"transfer-encoding", "connection", "keep-alive", "te", "trailers", "upgrade"}


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
    logger.info(f"Request headers: {dict(headers)}")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )

    logger.info(f"Response: {resp.status_code}")
    logger.info(f"Response headers: {dict(resp.headers)}")
    logger.info(f"Response body: {resp.content[:500]}")

    response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP}

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=response_headers,
    )
