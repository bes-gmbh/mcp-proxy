from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx
import os

app = FastAPI()

# Token-Mapping: Bearer Token → Confluence PAT
USER_MAP = {}
for key, value in os.environ.items():
    if key.startswith("USER_"):
        parts = value.split(":", 1)
        if len(parts) == 2:
            bearer_token, confluence_pat = parts
            USER_MAP[bearer_token] = confluence_pat

UPSTREAM = os.environ.get("MCP_UPSTREAM", "http://mcp-atlassian:9000")

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(request: Request, path: str):
    # Bearer Token aus Authorization Header lesen
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    bearer_token = auth_header.removeprefix("Bearer ").strip()
    confluence_pat = USER_MAP.get(bearer_token)

    if not confluence_pat:
        raise HTTPException(status_code=403, detail="Unknown token")

    # Headers weiterleiten, Authorization ersetzen
    headers = dict(request.headers)
    headers["Authorization"] = f"Bearer {confluence_pat}"
    headers.pop("host", None)

    # Request an mcp-atlassian weiterleiten
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.request(
            method=request.method,
            url=f"{UPSTREAM}/{path}",
            headers=headers,
            content=await request.body(),
            params=request.query_params,
        )

    return StreamingResponse(
        content=response.aiter_bytes(),
        status_code=response.status_code,
        headers=dict(response.headers),
    )
