import os
import httpx
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent, ImageContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route, Mount
import uvicorn

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
        )
    ]


@app_mcp.call_tool()
async def call_tool(name: str, arguments: dict):
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


starlette_app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse.handle_post_message),
    ]
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(starlette_app, host="0.0.0.0", port=port)
