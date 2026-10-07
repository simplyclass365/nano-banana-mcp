"""scrape_google_maps tool: Google Maps business listings as lead rows.

Drives the scraping engine from Google Maps Scraper Kit
(https://github.com/Mahanaicoach/google-maps-scraper-kit), a local gosom/google-maps-scraper
REST API started with `docker compose up -d`: create a job, poll it, download the CSV, trim it
to lead fields. Lead fields, job defaults and the social-profile regexes are adapted from the
kit's scripts/scrape.py (MIT, Copyright (c) 2026 Mahan).
"""
import asyncio
import csv
import io
import json
import os
import re
import time

import httpx
from mcp.types import Tool, TextContent

SCRAPER_BASE_URL = os.environ.get("SCRAPER_BASE_URL", "http://localhost:8080").rstrip("/")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")
UA = "nano-banana-mcp/1.0 (+https://github.com/simplyclass365/nano-banana-mcp)"
POLL_INTERVAL = 5

# The fields you contact and qualify a lead with; the engine returns ~34 raw columns.
LEAD_FIELDS = ["title", "phone", "emails", "website", "category", "address", "review_rating", "review_count"]
SOCIAL_FIELDS = ["instagram", "facebook", "linkedin"]

SCRAPE_TOOL = Tool(
    name="scrape_google_maps",
    description=(
        "Scrape Google Maps business listings into lead rows (name, phone, emails, website, category, "
        "address, rating, review count) with a Google Maps Scraper Kit engine. Scrapes run as jobs that "
        "take minutes: if the job is still running when wait_seconds runs out, the result returns its "
        "job_id. Call again with that job_id (and the same output options) to keep waiting."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Search terms with the location in them, e.g. 'plumbers in Denver CO'. All run as one job.",
            },
            "city": {"type": "string", "description": "Place to geocode for coordinates, e.g. 'Denver, CO'. Defaults to the first keyword."},
            "lat": {"type": ["number", "string"], "description": "Search centre latitude. Use with lon instead of city."},
            "lon": {"type": ["number", "string"], "description": "Search centre longitude. Use with lat instead of city."},
            "depth": {"type": "integer", "minimum": 1, "default": 5, "description": "Scroll depth, roughly how many listings per keyword. Higher is slower and more likely to be rate-limited."},
            "email": {"type": "boolean", "default": True, "description": "Visit each business website to extract emails (slower)."},
            "socials": {"type": "boolean", "default": False, "description": "Also find Instagram, Facebook and LinkedIn links on each business website."},
            "fast_mode": {"type": "boolean", "default": False, "description": "Faster, reduced data, up to ~21 results per keyword."},
            "radius": {"type": "integer", "minimum": 1, "default": 10000, "description": "Search radius in meters."},
            "zoom": {"type": "integer", "default": 15},
            "lang": {"type": "string", "default": "en"},
            "max_time": {"type": "integer", "minimum": 1, "default": 600, "description": "Job time limit in seconds."},
            "proxies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Proxy URLs (socks5://, http://, https://, auth optional) the engine rotates. Recommended for large or repeated runs.",
            },
            "fields": {"type": "array", "items": {"type": "string"}, "description": "Columns to return instead of the default lead fields."},
            "full": {"type": "boolean", "default": False, "description": "Return all raw columns."},
            "output_format": {"type": "string", "enum": ["csv", "json"], "default": "csv"},
            "job_id": {"type": "string", "description": "Resume a job started by an earlier call instead of creating one."},
            "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 900, "default": 50, "description": "How long this call waits for the job before returning its job_id."},
        },
    },
)


def _scraper_client():
    headers = {"User-Agent": UA}
    if SCRAPER_API_KEY:
        headers["X-API-Key"] = SCRAPER_API_KEY
    return httpx.AsyncClient(base_url=SCRAPER_BASE_URL, headers=headers, timeout=60.0)


async def _api(client, method, path, body=None):
    resp = await client.request(method, path, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"Scraper API {method} {path} failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp


async def _geocode(place):
    """Place name -> (lat, lon) strings via OpenStreetMap Nominatim (free, ~1 req/sec)."""
    try:
        async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=30.0) as client:
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"format": "json", "limit": 1, "q": place},
            )
            resp.raise_for_status()
            hits = resp.json()
    except httpx.HTTPError as e:
        raise RuntimeError(f'Geocoding "{place}" failed ({e}). Pass lat and lon instead.') from e
    if not hits:
        raise ValueError(f'Could not geocode "{place}". Pass lat and lon or a clearer city.')
    return str(hits[0]["lat"]), str(hits[0]["lon"])


async def _wait_for_job(client, job_id, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        status = (await _api(client, "GET", f"/api/v1/jobs/{job_id}")).json().get("Status")
        remaining = deadline - time.monotonic()
        if status in ("ok", "failed") or remaining <= 0:
            return status
        await asyncio.sleep(min(POLL_INTERVAL, remaining))


# Social enrichment: one fetch per business website, links found by regex.
SOCIAL_RE = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook": re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/([A-Za-z0-9_.\-]+)", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/([A-Za-z0-9_.\-%]+)", re.I),
}
_SKIP_HANDLE = {"", "home", "pages", "people", "help", "about", "policies", "policy",
                "legal", "tos", "privacy", "settings", "sharer", "tr", "profile.php",
                "plugins", "dialog", "intent", "login", "share.php", "permalink.php",
                "p", "reel", "reels", "explore", "stories", "tv", "watch", "events",
                "groups", "marketplace", "gaming", "photo", "hashtag", "search"}


def _find_socials(html):
    out = dict.fromkeys(SOCIAL_FIELDS, "")
    for plat, rx in SOCIAL_RE.items():
        for m in rx.finditer(html):
            h = m.group(1).lower()
            if h in _SKIP_HANDLE:
                continue
            if plat == "facebook" and (h.isdigit() or len(h) < 3):  # skip junk like /2008
                continue
            out[plat] = m.group(0).rstrip("\"'/").replace("\\", "")
            break
    return out


async def _fetch_html(client, url, limit=400_000):
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    body = b""
    async with client.stream("GET", url) as resp:
        async for chunk in resp.aiter_bytes():
            body += chunk
            if len(body) >= limit:
                break
    return body[:limit].decode("utf-8", "replace")


async def _enrich_socials(rows, results, workers=8):
    sem = asyncio.Semaphore(workers)
    headers = {"User-Agent": f"Mozilla/5.0 (compatible; {UA})", "Accept": "text/html"}

    async def work(client, raw, result):
        result.update(dict.fromkeys(SOCIAL_FIELDS, ""))
        if not raw.get("website"):
            return
        async with sem:
            try:
                html = await asyncio.wait_for(_fetch_html(client, raw["website"]), timeout=15)
            except (httpx.HTTPError, httpx.InvalidURL, asyncio.TimeoutError):
                return
        result.update(_find_socials(html))

    async with httpx.AsyncClient(headers=headers, timeout=10.0, follow_redirects=True) as client:
        await asyncio.gather(*(work(client, raw, result) for raw, result in zip(rows, results)))


def _dedupe(rows):
    seen, out = set(), []
    for r in rows:
        key = r.get("place_id") or r.get("cid")
        if key:
            if key in seen:
                continue
            seen.add(key)
        out.append(r)
    return out


async def scrape_google_maps(arguments):
    job_id = arguments.get("job_id")
    notes = []
    if not job_id:
        keywords = list(dict.fromkeys(k.strip() for k in arguments.get("keywords", []) if k.strip()))
        if not keywords:
            raise ValueError("Pass keywords to start a scrape, or job_id to resume one.")
        lat, lon = arguments.get("lat"), arguments.get("lon")
        if lat is None or lon is None:
            lat, lon = await _geocode(arguments.get("city") or keywords[0])
        depth = arguments.get("depth", 5)
        if depth >= 15 or len(keywords) >= 10:
            notes.append(
                "Large job: running these back to back without proxies can get the scraper's IP "
                "temporarily rate-limited by Google. Pass proxies for big or repeated runs."
            )
        body = {
            "name": keywords[0][:60],
            "keywords": keywords,
            "lang": arguments.get("lang", "en"),
            "zoom": arguments.get("zoom", 15),
            "lat": str(lat),
            "lon": str(lon),
            "fast_mode": arguments.get("fast_mode", False),
            "radius": arguments.get("radius", 10000),
            "depth": depth,
            "email": arguments.get("email", True),
            "max_time": arguments.get("max_time", 600),
        }
        if arguments.get("proxies"):
            body["proxies"] = arguments["proxies"]

    try:
        async with _scraper_client() as client:
            if not job_id:
                job_id = (await _api(client, "POST", "/api/v1/jobs", body)).json().get("id")
                if not job_id:
                    raise RuntimeError("Scraper did not return a job id.")
            status = await _wait_for_job(client, job_id, arguments.get("wait_seconds", 50))
            if status == "failed":
                raise RuntimeError(
                    f"Scrape job {job_id} failed. If this keeps happening the scraper's IP may be "
                    "rate-limited: wait, lower depth, or pass proxies."
                )
            if status != "ok":
                return [TextContent(type="text", text="\n".join([
                    f"Scrape job {job_id} is still running (status: {status}). Call scrape_google_maps "
                    f'again with job_id="{job_id}" and the same output options to keep waiting.',
                    *notes,
                ]))]
            csv_text = (await _api(client, "GET", f"/api/v1/jobs/{job_id}/download")).text
    except httpx.TransportError as e:
        raise RuntimeError(
            f"Could not reach the Google Maps scraper at {SCRAPER_BASE_URL} ({e!r}). Start it with "
            "`docker compose up -d` in google-maps-scraper-kit, or set SCRAPER_BASE_URL."
        ) from e

    rows = _dedupe(list(csv.DictReader(io.StringIO(csv_text))))
    if arguments.get("full"):
        fields = list(rows[0].keys()) if rows else LEAD_FIELDS
    else:
        fields = arguments.get("fields") or LEAD_FIELDS
    results = [{k: r.get(k, "") for k in fields} for r in rows]

    if arguments.get("socials"):
        await _enrich_socials(rows, results)
        fields = fields + SOCIAL_FIELDS
        found = sum(1 for r in results if any(r[k] for k in SOCIAL_FIELDS))
        notes.append(f"Social profiles found for {found}/{len(results)} businesses.")

    if not results:
        notes.append(
            "No results. The keyword may be too narrow or the coordinates wrong (try a wider radius), "
            "or the scraper's IP may be rate-limited."
        )

    if arguments.get("output_format") == "json":
        data = json.dumps(results, indent=2, ensure_ascii=False)
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
        data = buf.getvalue()

    summary = "\n".join([f"Scrape job {job_id}: {len(results)} businesses. Fields: {', '.join(fields)}.", *notes])
    return [TextContent(type="text", text=summary), TextContent(type="text", text=data)]
