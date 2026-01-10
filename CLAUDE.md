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
  --set-env-vars "FLASK_ENV=production,SEARCH_WORKERS=15,DETAIL_WORKERS=10" \
  --set-secrets "CR_API_KEY=cr-api-key:latest,CR_FINDER_PASSWORD=cr-finder-password:latest,FLASK_SECRET_KEY=flask-secret-key:latest"
```

#### Secrets (managed via GCP Secret Manager)
- `cr-api-key` - Clash Royale API key
- `cr-finder-password` - Login password
- `flask-secret-key` - Session encryption

#### Cloud Run Notes
- Project: `cr-tournament-finder`
- Region: `europe-west3` (Frankfurt)
- 1GB RAM allows 15 parallel search workers (faster than Render)
- Cold start ~5-10s when idle
- **No auto-deploy**: Code is uploaded from local machine via `--source .`, not pulled from GitHub
- Redeploy manually after code changes by running the deployment command above

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
| `SEARCH_WORKERS` | Parallel search threads (default: 10) |
| `DETAIL_WORKERS` | Parallel detail fetch threads (default: 10) |

### Shared Deployment Notes
- Uses RoyaleAPI Proxy (`proxy.royaleapi.dev`) to bypass IP restrictions
- Gunicorn with 300s timeout for long searches
- Dockerfile included for containerized deployment

## Architecture

### Backend (`app.py`)
Flask server with these key components:

- **Authentication**: Session-basierte Auth mit 30-Tage Cookie. Passwort über `CR_FINDER_PASSWORD` Env-Var.

- **Tournament Fetching**: `fetch_all_tournaments()` searches all 2-letter combinations (aa-zz = 676 queries) plus common words in parallel via ThreadPoolExecutor. Drills down to 3+ letter queries when hitting the API's ~20 result limit. Deduplicates by tag.

- **Detail Fetching**: `fetch_tournament_details_batch()` fetches individual tournament details to get accurate `startedTime` (since hosts can start early before the max prep time).

- **Time Calculation**: `calc_remaining_minutes()` and `calc_elapsed_minutes()` use `startedTime` from detail API when available, falling back to estimated time (createdTime + preparationDuration).

- **Filtering**: `filter_tournaments()` applies filters in two phases - non-time filters first (to reduce set), then time filters after detail fetch.

### Frontend (`templates/index.html`, `static/app.js`, `static/style.css`)
Vanilla HTML/CSS/JS with no build step. Frontend sends heartbeat every 30s to keep server alive.

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

### Data Files
- `config.json`: Stores API key and saved filter defaults
- `game_modes.json`: Maps game mode IDs (e.g., 72000009) to human-readable names
- `logs/tournament_finder.log`: Rotating log file (max 5MB, 3 backups) with search stats, found tournaments, and filter results

### Logging & Debug
- Backend logs to both console and `logs/tournament_finder.log`
- Logs include: filters used, tournaments found by game mode, API stats (queries, drill-downs, rate-limits, errors)
- Frontend has collapsible debug panel showing search stats, game mode breakdown, and tag search

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Serve main page (requires auth) |
| `/login` | GET/POST | Login page |
| `/logout` | GET | Clear session |
| `/manifest.json` | GET | PWA manifest |
| `/api/tournaments` | GET | Fetch and filter tournaments |
| `/api/game-modes` | GET | Get game mode ID → name mapping |
| `/api/config` | GET/POST | Read/save API key and filter defaults |
| `/api/logs` | GET | Get log file contents (params: `lines`, `search`) |
| `/api/heartbeat` | POST | Keep server alive |
| `/api/shutdown` | POST | Stop the server |

## Clash Royale API Notes

- API key from https://developer.clashroyale.com (IP-locked)
- Tournament search endpoint (`/tournaments?name=X`) returns max ~20 results per query regardless of limit parameter
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
