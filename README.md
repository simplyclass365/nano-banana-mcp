# nano-banana-mcp

MCP server (SSE at `/sse`) with two tools:

- `generate_image`: image generation with Nano Banana (Gemini). Needs `GEMINI_API_KEY`.
- `scrape_google_maps`: Google Maps business listings as lead rows (name, phone, emails, website,
  category, address, rating, review count), with optional Instagram, Facebook and LinkedIn
  enrichment. Runs on the scraping engine from
  [Google Maps Scraper Kit](https://github.com/Mahanaicoach/google-maps-scraper-kit).

```bash
pip install -r requirements.txt
python server.py   # listens on $PORT, default 8000
```

## Google Maps scraper setup

`scrape_google_maps` drives the kit's local scraper API. Start it with Docker:

```bash
git clone https://github.com/Mahanaicoach/google-maps-scraper-kit.git
cd google-maps-scraper-kit
docker compose up -d   # API on http://127.0.0.1:8080
```

| Env var | Default | Purpose |
|---|---|---|
| `SCRAPER_BASE_URL` | `http://localhost:8080` | Where the scraper API is |
| `SCRAPER_API_KEY` | empty | Sent as `X-API-Key`, for a scraper behind an auth proxy |

The scraper API has no authentication, so the kit binds it to `127.0.0.1`. If this server runs on
another machine, don't expose the scraper publicly: put it behind an auth proxy that checks
`X-API-Key` and set `SCRAPER_API_KEY`.

Scrapes run as jobs that take minutes. The tool waits up to `wait_seconds` (default 50), then returns
the job's `job_id` if it is still running; call the tool again with that `job_id` to keep waiting.
The search location comes from `lat`/`lon`, or `city` geocoded with OpenStreetMap Nominatim, or
failing both the first keyword.

### Responsible use

Scraping Google Maps is against Google's Terms of Service, and heavy use without proxies can get the
scraper's IP temporarily rate-limited. Start at `depth` 5, run one job at a time, and pass `proxies`
for large or repeated runs. Phones and emails are personal data: GDPR, CCPA and CAN-SPAM apply.

### Credits

Scraping engine: [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper)
(MIT, Copyright (c) 2023 Georgios Komninos). Lead fields, job defaults and social-profile matching
are adapted from [Google Maps Scraper Kit](https://github.com/Mahanaicoach/google-maps-scraper-kit)
(MIT, Copyright (c) 2026 Mahan).
