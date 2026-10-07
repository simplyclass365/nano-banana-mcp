import contextlib
import hmac
import os
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import Tool, TextContent, ImageContent
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route, Mount
import uvicorn

from gmaps_scraper import SCRAPE_TOOL, XLSX_MIME, export_file, scrape_google_maps

# When set, every endpoint but /health, /downloads/ (unguessable file names) and /messages/
# (SSE posts, keyed by a session id that only an authorized /sse connection receives) requires
# it as ?token=... or an "Authorization: Bearer ..." header.
MCP_ACCESS_TOKEN = os.environ.get("MCP_ACCESS_TOKEN", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-flash-image-preview"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    + GEMINI_MODEL
    + ":generateContent?key="
    + GEMINI_API_KEY
)

app_mcp = Server("nano-banana-image-gen")


@app_mcp.list_tools()
async def list_tools():
    return [
        Tool(
            name="generate_image",
            description="Generate image using Nano Banana.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Image description."},
                    "style": {"type": "string", "default": "photorealistic"},
                    "aspect_ratio": {"type": "string", "enum": ["1:1", "16:9", "9:16"], "default": "1:1"},
                },
                "required": ["prompt"],
            },
        ),
        SCRAPE_TOOL,
    ]


@app_mcp.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "scrape_google_maps":
        request = app_mcp.request_context.request
        return await scrape_google_maps(arguments, base_url=str(request.base_url) if request else None)
    if name != "generate_image":
        raise ValueError(f"Unknown tool: {name}")
    prompt = arguments["prompt"]
    style = arguments.get("style", "photorealistic")
    aspect_ratio = arguments.get("aspect_ratio", "1:1")
    full_prompt = f"{prompt}. Style: {style}. Aspect ratio: {aspect_ratio}."
    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(GEMINI_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    results = []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if "text" in part:
                results.append(TextContent(type="text", text=part["text"]))
            elif "inlineData" in part:
                inline = part["inlineData"]
                results.append(ImageContent(type="image", data=inline["data"], mimeType=inline.get("mimeType", "image/png")))
    if not results:
        results.append(TextContent(type="text", text="No image generated."))
    return results


sse = SseServerTransport("/messages/")


async def handle_sse(request: Request):
    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await app_mcp.run(
            streams[0], streams[1], app_mcp.create_initialization_options()
        )
    return Response()


session_manager = StreamableHTTPSessionManager(app=app_mcp, stateless=True)


class StreamableHTTPApp:
    async def __call__(self, scope, receive, send):
        await session_manager.handle_request(scope, receive, send)


async def handle_download(request: Request):
    found = export_file(request.path_params["name"])
    if not found:
        return PlainTextResponse("Not found or expired.", status_code=404)
    path, filename = found
    return FileResponse(path, media_type=XLSX_MIME, filename=filename)


async def handle_health(request: Request):
    return PlainTextResponse("ok")


class RequireToken:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        public = path == "/health" or path.startswith(("/downloads/", "/messages/"))
        if MCP_ACCESS_TOKEN and scope["type"] == "http" and not public:
            request = Request(scope)
            token = request.query_params.get("token") or request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(token.encode(), MCP_ACCESS_TOKEN.encode()):
                await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        yield


starlette_app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
        Route("/mcp", endpoint=StreamableHTTPApp()),
        Route("/downloads/{name}", endpoint=handle_download),
        Route("/health", endpoint=handle_health),
    ],
    middleware=[Middleware(RequireToken)],
    lifespan=lifespan,
)

if __name__ == "__main__":
    if not MCP_ACCESS_TOKEN:
        print("WARNING: MCP_ACCESS_TOKEN is not set, so anyone who knows this server's URL can use its tools.")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(starlette_app, host="0.0.0.0", port=port)
