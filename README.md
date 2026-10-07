# nano-banana-mcp

Remote MCP server with two tools:

- `generate_image`: image generation with Nano Banana (Gemini). Needs `GEMINI_API_KEY`.
- `scrape_google_maps`: Google Maps business listings as lead rows (name, phone, emails, website,
  category, address, rating, review count), with optional Instagram, Facebook and LinkedIn
  enrichment, and a download link to an Excel file of the results. Runs on the scraping engine from
  [Google Maps Scraper Kit](https://github.com/Mahanaicoach/google-maps-scraper-kit).

Endpoints: `/mcp` (Streamable HTTP, use this one), `/sse` (legacy SSE), `/downloads/<file>` (Excel
exports), `/health`.

```bash
pip install -r requirements.txt
python server.py   # listens on $PORT, default 8000
```

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `MCP_ACCESS_TOKEN` | empty | When set, every endpoint except `/health` and the Excel download links requires it as `?token=...` or `Authorization: Bearer ...`. Always set it on a public server. |
| `GEMINI_API_KEY` | empty | For `generate_image` |
| `SCRAPER_BASE_URL` | `http://localhost:8080` | Where the scraper API is |
| `SCRAPER_API_KEY` | empty | Sent as `X-API-Key`, for a scraper behind an auth proxy |
| `HOURLY_CONTACT_LIMIT` | `100` | Max contacts `scrape_google_maps` returns in any 60 minutes |
| `DAILY_CONTACT_LIMIT` | `1000` | Max contacts it returns in any 24 hours (set `500` for a stricter cap) |
| `DATA_DIR` | `$RAILWAY_VOLUME_MOUNT_PATH`, else `./data` | Usage counters and Excel exports |
| `PUBLIC_BASE_URL` | `https://$RAILWAY_PUBLIC_DOMAIN`, else the request's host | Base of the Excel download links |

## Scraping limits

Every business row the tool returns counts as one contact. The hourly and daily limits are rolling
windows (the last 60 minutes and the last 24 hours), shared by everyone using the server, and stored
in `DATA_DIR` so they survive restarts. When a job finds more businesses than the remaining
allowance, the tool returns the first ones that fit and says how long until more are available; once
the allowance is used up, it refuses to start new jobs. Fetching a job's results again with its
`job_id` doesn't count twice.

The limits cap what the tool returns, not what the engine scrapes, so keep `depth` low (the default
is 5) to avoid scraping listings you can't receive.

## Excel output

Each scrape with results also saves an `.xlsx` file (bold header, frozen first row, filters) and
returns its download link. Links have unguessable names and expire after 24 hours. Pass
`"excel": false` to skip it.

## Deploy on Railway

You need two services in one Railway project: this server, and the scraping engine on Railway's
private network.

1. **Scraping engine.** New service, Docker image `gosom/google-maps-scraper:v1.18.1`; name it
   `google-maps-scraper`. In its settings:
   - Start command: `google-maps-scraper -web -data-folder /gmapsdata`
   - Volume mounted at `/gmapsdata`
   - Variable `RAILWAY_SHM_SIZE_BYTES=1073741824` (headless Chromium needs more than the 64 MB default)
   - **No public domain.** The engine has no authentication; keep it on the private network.
2. **This server.** New service from this GitHub repo. `railway.json` sets the start command and
   health check. In its settings:
   - Variables:
     - `SCRAPER_BASE_URL=http://${{google-maps-scraper.RAILWAY_PRIVATE_DOMAIN}}:8080`
     - `MCP_ACCESS_TOKEN=` a long random string, e.g. from `python -c "import secrets; print(secrets.token_urlsafe(32))"`
     - `GEMINI_API_KEY`, if you use `generate_image`
   - Volume mounted at `/data`, so usage counts and Excel files survive redeploys
   - Generate a public domain under Networking

Headless Chrome needs about 1 to 2 GB of RAM, so expect usage beyond the smallest Railway plan.
Google blocks datacenter IPs sooner than home connections; if jobs start failing or coming back
empty, pass `proxies`.

## Use it in Claude

In Claude (web, desktop or mobile, on a Pro, Max, Team or Enterprise plan): **Settings → Connectors →
Add custom connector**, and enter

```
https://<your-railway-domain>/mcp?token=<MCP_ACCESS_TOKEN>
```

Then turn the connector on from the tools menu in any chat and ask, for example, "Scrape dentists in
Denver, CO and give me the Excel file."

Scrapes run as jobs that take minutes. The tool waits up to `wait_seconds` (default 50), then returns
the job's `job_id` if it is still running, and Claude calls it again with that `job_id` to keep
waiting. The search location comes from `lat`/`lon`, or `city` geocoded with OpenStreetMap Nominatim,
or failing both the first keyword.

## Run the scraper locally instead

```bash
git clone https://github.com/Mahanaicoach/google-maps-scraper-kit.git
cd google-maps-scraper-kit
docker compose up -d   # API on http://127.0.0.1:8080
```

The kit binds the engine to `127.0.0.1` because it has no authentication. If this server runs on
another machine, put the engine behind an auth proxy that checks `X-API-Key` and set
`SCRAPER_API_KEY`.

## Responsible use

Scraping Google Maps is against Google's Terms of Service, and heavy use without proxies can get the
scraper's IP temporarily rate-limited. Run one job at a time, start at `depth` 5, and pass `proxies`
for large or repeated runs. Phones and emails are personal data: GDPR, CCPA and CAN-SPAM apply.

## Credits

Scraping engine: [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper)
(MIT, Copyright (c) 2023 Georgios Komninos). Lead fields, job defaults and social-profile matching
are adapted from [Google Maps Scraper Kit](https://github.com/Mahanaicoach/google-maps-scraper-kit)
(MIT, Copyright (c) 2026 Mahan).
