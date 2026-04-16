import os
import logging
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("mcp-proxy")

app = FastAPI(title="MCP User-Token Proxy")

TOKEN_MAP: dict[str, tuple[str, str]] = {}
MCP_UPSTREAM = os.environ.get("MCP_UPSTREAM", "http://mcp-atlassian:9000")

for key, value in os.environ.items():
    if key.startswith("USER_"):
        username = key[5:]
        parts = value.split(":", 1)
        if len(parts) == 2:
            innogpt_token, confluence_pat = parts
            TOKEN_MAP[innogpt_token.strip()] = (username, confluence_pat.strip())
            log.info(f"User geladen: {username}")

log.info(f"Proxy bereit – {len(TOKEN_MAP)} User geladen | Upstream: {MCP_UPSTREAM}")

@app.get("/health")
async def health():
    return {"status": "ok", "users": len(TOKEN_MAP), "upstream": MCP_UPSTREAM}

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(request: Request, path: str):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return Response(content="Unauthorized", status_code=401)

    incoming_token = auth_header.removeprefix("Bearer ").strip()

    if incoming_token not in TOKEN_MAP:
        return Response(content="Unauthorized", status_code=401)

    username, confluence_pat = TOKEN_MAP[incoming_token]
    log.info(f"✓ {username} | {request.method} /{path}")

    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in ("authorization", "host", "content-length")
    }
    forward_headers["Authorization"] = f"Bearer {confluence_pat}"
    forward_headers["X-User"] = username

    query = request.url.query
    upstream_url = f"{MCP_UPSTREAM}/{path}"
    if query:
        upstream_url += f"?{query}"

    body = await request.body()
    accept = request.headers.get("accept", "")
    is_streaming = "text/event-stream" in accept

    async with httpx.AsyncClient(timeout=None) as client:
        if is_streaming:
            async def stream_response():
                async with client.stream(
                    request.method,
                    upstream_url,
                    headers=forward_headers,
                    content=body,
                ) as upstream:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk

            return StreamingResponse(
                stream_response(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        else:
            upstream_resp = await client.request(
                request.method,
                upstream_url,
                headers=forward_headers,
                content=body,
            )
            return Response(
                content=upstream_resp.content,
                status_code=upstream_resp.status_code,
                headers=dict(upstream_resp.headers),
            )
