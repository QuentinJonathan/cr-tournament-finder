# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clash Royale Tournament Finder - A local web app that fetches and filters tournaments from the official Clash Royale API. Users can search for open tournaments based on game mode, player count, level cap, and time remaining.

## Running the Application

```bash
# Install dependencies (if needed)
pip3 install -r requirements.txt

# Run the server
python3 app.py

# Or double-click the launcher
open "Launch Tournament Finder.command"
```

The app runs at `http://localhost:5050`.

## Production Deployment

### Primary: Google Cloud Run

**Live URL:** https://cr-tournament-finder-98463050344.europe-west3.run.app

**Hosting:** Google Cloud Run (Free Tier - 1GB RAM, 300s timeout, ~3000 searches/month free)

#### Deployment (from local machine, not GitHub)

**Easiest way:** double-click `Deploy to Cloud Run.command` in the project root. It finds gcloud, runs the full deploy command below, and mirrors all output to `deploy.log`.

Manual alternative:
```bash
# If gcloud is not in PATH, use full path (Homebrew install):
# /opt/homebrew/share/google-cloud-sdk/bin/gcloud

# Run from project root directory
gcloud run deploy cr-tournament-finder \
  --source . \
  --region europe-west3 \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 300 \
  --concurrency 10 \
  --min-instances 0 \
  --max-instances 1 \
  --execution-environment gen2 \
  --add-volume name=config,type=cloud-storage,bucket=cr-tournament-finder-config \
  --add-volume-mount volume=config,mount-path=/data \
  --set-env-vars "FLASK_ENV=production,CONFIG_PATH=/data/config.json,SEARCH_WORKERS=25,DETAIL_WORKERS=50,VERIFY_WORKERS=5,MAX_VERIFICATION_PASSES=2,QUERY_DRILLDOWN_THRESHOLD=20" \
  --set-secrets "CR_API_KEY=cr-api-key:latest,CR_FINDER_PASSWORD=cr-finder-password:latest,FLASK_SECRET_KEY=flask-secret-key:latest"
```

#### Secrets (managed via GCP Secret Manager)
- `cr-api-key` - Clash Royale API key
- `cr-finder-password` - Login password
- `flask-secret-key` - Session encryption

#### Cloud Run Notes
- Project: `cr-tournament-finder`
- Region: `europe-west3` (Frankfurt)
- **Persistent config**: GCS bucket `cr-tournament-finder-config` is mounted at `/data` (requires gen2 execution environment); `CONFIG_PATH=/data/config.json` makes saved filter defaults survive instance restarts and sync across all devices. The Cloud Run service account has `roles/storage.objectAdmin` on the bucket.
- Current production tuning favors completeness over raw fan-out: `SEARCH_WORKERS=25`, `DETAIL_WORKERS=50`, `VERIFY_WORKERS=5`
- The crawler now exposes a confidence signal (`high` / `medium` / `low`) plus unresolved and saturated query counts in the debug panel
- Cold start ~5-10s when idle
- **No auto-deploy**: Code is uploaded from local machine via `--source .`, not pulled from GitHub
- Redeploy manually after code changes via `Deploy to Cloud Run.command` (or the command above)
- **PWA cache**: If changing `style.css` or `app.js`, bump `CACHE_NAME` in `static/service-worker.js` (see PWA section)

#### Finding gcloud CLI (Homebrew)
If `gcloud` command is not found after installing via `brew install google-cloud-sdk`:
```bash
# Full path (Homebrew on Apple Silicon):
/opt/homebrew/share/google-cloud-sdk/bin/gcloud

# Add to PATH permanently (add to ~/.zshrc):
source "/opt/homebrew/share/google-cloud-sdk/path.zsh.inc"
source "/opt/homebrew/share/google-cloud-sdk/completion.zsh.inc"
```

### Backup: Render.com

**URL:** https://cr-tournament-finder.onrender.com

**Hosting:** Render.com (Free Tier - 512MB RAM, sleeps after 15 min inactivity)

#### Environment Variables (Render Dashboard)

| Variable | Purpose |
|----------|---------|
| `CR_API_KEY` | Clash Royale API key (uses RoyaleAPI proxy, IP `45.79.218.79`) |
| `CR_FINDER_PASSWORD` | Login password for web UI |
| `FLASK_SECRET_KEY` | Session encryption key (auto-generated) |
| `FLASK_ENV` | Set to `production` |
| `SEARCH_WORKERS` | Parallel search queries (recommended: 25) |
| `DETAIL_WORKERS` | Parallel detail fetch queries (recommended: 50) |
| `VERIFY_WORKERS` | Low-concurrency verification pass for failed search branches (recommended: 5) |
| `MAX_VERIFICATION_PASSES` | How often failed search branches are retried after the main crawl (recommended: 2) |
| `QUERY_DRILLDOWN_THRESHOLD` | Threshold for query saturation and drill-down detection (default: 20) |

### Shared Deployment Notes
- Uses RoyaleAPI Proxy (`proxy.royaleapi.dev`) to bypass IP restrictions
- Gunicorn with 300s timeout for long searches
- **Gunicorn runs 1 worker (multi-threaded) by default** — the search/detail caches are in-process, so multiple workers would each crawl separately. Scale via `GUNICORN_THREADS`, not `WEB_CONCURRENCY`.
- Dockerfile included for containerized deployment

## Architecture

### Backend (`app.py`)
Flask server with these key components:

- **Authentication**: Session-basierte Auth mit 30-Tage Cookie. Passwort über `CR_FINDER_PASSWORD` Env-Var.

- **Tournament Fetching**: `fetch_all_tournaments()` uses async HTTP to search all 2-letter combinations (aa-zz = 676 queries), single latin letters (for one-character names), single digits (0-9), accented latin letters (à-ž, since the API does no accent folding), Cyrillic letters (а-я = 33 queries), Arabic-script letters/digits, plus common words. The main crawl runs with bounded concurrency, retries transient failures with backoff, and rechecks failed query branches in a verification phase. Results are deduplicated by tag and annotated with a search confidence level.

- **Caching & Performance Knobs**:
  - Search results are cached in-memory for `SEARCH_CACHE_TTL_SECONDS` (default: 180s). `/api/tournaments/search?force=1` forces a fresh crawl.
  - Detail responses are cached in-memory for `DETAIL_CACHE_TTL_SECONDS` (default: 300s).
  - Drill-down depth is configurable: `MAX_QUERY_LEN` (default: 4), `MAX_CYRILLIC_QUERY_LEN` (default: 2), `MAX_ARABIC_QUERY_LEN` (default: 3).
  - Crawl robustness is configurable: `SEARCH_WORKERS`, `DETAIL_WORKERS`, `VERIFY_WORKERS`, `MAX_VERIFICATION_PASSES`, `QUERY_DRILLDOWN_THRESHOLD`.

- **Detail Fetching**: `fetch_tournament_details_batch()` fetches individual tournament details in parallel to get accurate `startedTime` (since hosts can start early before the max prep time). For performance, details are only fetched for `inProgress` tournaments.

- **Time Calculation**: `calc_remaining_minutes()` and `calc_elapsed_minutes()` use `startedTime` from detail API when available, falling back to estimated time (createdTime + preparationDuration).

- **Filtering**: Server-side `filter_tournaments()` for legacy endpoint. Client-side filtering in `app.js` for instant filter updates after initial fetch.

- **Search Confidence**: The crawler reports `confidence`, `retriedQueries`, `failedQueries`, `verificationPasses`, and `saturatedQueries`. `high` means no unresolved failed branches and no saturated leaf queries remained after verification.

### Frontend (`templates/index.html`, `static/app.js`, `static/style.css`)
Vanilla HTML/CSS/JS with no build step. Frontend sends heartbeat every 30s to keep server alive.

**Client-Side Filtering:**
- Initial search fetches ALL tournaments via `/api/tournaments/search` (SSE stream preferred)
- Results cached in `state.tournaments`; filter changes apply instantly without API calls
- Time calculations done in JavaScript (`parseCrTime`, `computeCountdown`)
- Client derives an `effectiveStatus` per tick (prep → live → ended transitions happen live without refresh); ended tournaments are always hidden
- Refresh button: normal click reuses the server cache, Shift-click forces a full re-crawl
- AUTO pill toggles auto-refresh (~every 3 min while tab is visible, persisted in localStorage)
- Favorited tournaments trigger a browser notification when they go live (Notification API)

**UI Theme (January 2026):**
- Dark gaming aesthetic with Clash Royale-inspired colors
- Color palette: Deep purple (`#1a1a2e`), gold (`#f4d03f`), electric blue (`#00d4ff`)
- Glassmorphism cards with `backdrop-filter: blur(12px)`
- Google Fonts: Inter (UI), JetBrains Mono (code/tags)

**Filter Components:**
- Pill buttons for game mode selection (replaces multi-select dropdown)
- Toggle button group for level cap (11-16)
- Filter count badge on search button
- "Clear Filters" ghost button

**Results Display:**
- Desktop: Enhanced table with styled status badges
- Mobile (< 768px): Card-based layout with time progress bars
- Staggered fade-in animations for results
- Pulsing dot indicator for "In Progress" tournaments
- **Join buttons**: One-click deep links to join tournaments directly in Clash Royale app
  - Gold-accented styling to stand out
  - Lock icon for password-protected tournaments (password required in-game)

**Animations:**
- Entrance: `fadeInUp` for cards, `fadeInDown` for header
- Buttons: scale + glow on hover
- Toast: slide-up with bounce easing
- Status badges: pulse animation for active tournaments

### PWA (Progressive Web App)
The app is installable as a standalone app on mobile and desktop.

**Files:**
- `manifest.json`: App name, icons, theme colors, display mode
- `static/service-worker.js`: Static asset caching, offline fallback
- `static/icons/`: App icons (192x192, 512x512, maskable, apple-touch-icon, favicon)

**Features:**
- Standalone display mode (no browser UI)
- Gold theme color (`#f4d03f`) in address bar
- Offline fallback page when no connection
- Cache-first for static assets, network-first for HTML
- iOS support via apple-mobile-web-app meta tags

**Installation:**
- Android: Chrome menu → "Install app" or "Add to Home screen"
- iOS: Safari → Share → "Add to Home Screen"
- Desktop: Chrome address bar install icon

**Testing:**
- Chrome DevTools → Application → Manifest (check for errors)
- Lighthouse → PWA audit (should show "Installable")

**Cache Invalidation (IMPORTANT for deployments):**

When deploying changes to static assets (`style.css`, `app.js`), you MUST bump the cache version in `static/service-worker.js`:

```javascript
// Change this version number (e.g., v9 -> v10)
const CACHE_NAME = 'cr-finder-v9';
```

Why: The service worker uses cache-first for static assets. Without bumping the version:
- Users keep seeing old cached CSS/JS
- Changes won't appear until they manually clear browser data

The version bump triggers:
1. Browser detects service worker file changed
2. New service worker installs with new cache name
3. `skipWaiting()` activates it immediately
4. Old cache gets deleted automatically
5. Fresh assets are fetched and cached

### Data Files
- `config.json`: Stores API key and saved filter defaults
- `game_modes.json`: Maps game mode IDs (e.g., 72000009) to human-readable names
- `logs/tournament_finder.log`: Rotating log file (max 5MB, 3 backups) with search stats, found tournaments, and filter results

### Logging & Debug
- Backend logs to both console and `logs/tournament_finder.log`
- Logs include: filters used, tournaments found by game mode, API stats (queries, retries, verification passes, unresolved branches, rate-limits, errors, confidence)
- Frontend has collapsible debug panel showing search stats, confidence, game mode breakdown, and tag search

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serve main page (requires auth) |
| `/login` | GET/POST | Login page |
| `/logout` | GET | Clear session |
| `/manifest.json` | GET | PWA manifest |
| `/api/tournaments` | GET | Fetch and filter tournaments (legacy, server-side filtering) |
| `/api/tournaments/search` | GET | Fetch ALL tournaments with raw time fields (for client-side filtering) |
| `/api/tournaments/search/stream` | GET | SSE: Stream search progress + final payload (preferred by UI) |
| `/api/game-modes` | GET | Get game mode ID → name mapping |
| `/api/config` | GET/POST | Read/save API key and filter defaults |
| `/api/logs` | GET | Get log file contents (params: `lines`, `search`) |
| `/api/heartbeat` | POST | Keep server alive |
| `/api/shutdown` | POST | Stop the server |

## Clash Royale API Notes

- API key from https://developer.clashroyale.com (IP-locked)
- Tournament search endpoint (`/tournaments?name=X`) — measured semantics (June 2026, via RoyaleAPI proxy):
  - **Word-prefix matching**: a query matches if it is a prefix of any whitespace-separated word in the tournament name (`live` matches "a a a 5 **live**s", but `alive` does NOT match "aaalive"). Multi-word queries appear to OR their tokens.
  - **Case-insensitive**, but **no accent/Unicode folding**: `cok` does NOT match "çok hawali"; exotic characters (ç, é, CJK, emoji) only match when queried with the exact character.
  - **Response cap is exactly 20 items**; the `limit` query param is ignored and no paging cursors are returned even when capped — drill-down is the only way to get full coverage. The crawler's `QUERY_DRILLDOWN_THRESHOLD=20` matches this cap exactly.
  - Capped responses are roughly recency-biased but not strictly sorted by `createdTime`.
  - 1-character queries are accepted (no minimum length issue via the proxy).
  - Coverage implication: tournaments whose every word starts with a non-probed character (accented latin, emoji, CJK) are invisible to the crawler. Measured on a live corpus of 161 tournaments: ~1.3% of word-starts are such characters, so the expected blind spot is ~1-2 tournaments at any time. A 37-probe spot-check found 0 tournaments missed by the crawler.
- Tournament detail endpoint (`/tournaments/{tag}`) returns `startedTime` and `endedTime` fields not available in search
- The `preparationDuration` field is the MAX prep time - hosts can start early
- Tournament statuses: `inPreparation`, `inProgress`, `ended`
- Time format: `YYYYMMDDTHHmmss.sssZ` (e.g., `20260105T220549.000Z`)
- Tags must be URL-encoded (# → %23) when used in URLs

### Deep Links (Join Tournament)
- Format: `https://link.clashroyale.com/en?clashroyale://joinTournament?id=TAG`
- TAG should be without the `#` prefix (e.g., `2PQGYYGY` not `#2PQGYYGY`)
- Opens Clash Royale app directly to the tournament join screen
- Password-protected tournaments: link works but password must be entered in-game
