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

## Deployment Status (WIP)

### Was bereits erledigt ist:
- [x] Auto-Shutdown entfernt (für Server-Deployment)
- [x] Session-basierte Authentifizierung hinzugefügt (`/login`, 30-Tage Cookie)
- [x] API-Key über Environment Variable (`CR_API_KEY`)
- [x] RoyaleAPI Proxy aktiviert (`proxy.royaleapi.dev` statt `api.clashroyale.com`)
- [x] `wsgi.py` für Gunicorn erstellt
- [x] `Procfile` für Render erstellt
- [x] `.gitignore` erstellt
- [x] GitHub Repo erstellt: https://github.com/QuentinJonathan/cr-tournament-finder (public)

### Nächster Schritt: Render.com Deployment

Manuell im Render Dashboard erstellen (Free Tier):

1. https://dashboard.render.com → "New +" → "Web Service" → "Public Git repository"
2. Repo URL: `https://github.com/QuentinJonathan/cr-tournament-finder`
3. Settings:
   - Name: `cr-tournament-finder`
   - Region: Frankfurt
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn wsgi:app`
   - Instance Type: **Free**
4. Environment Variables hinzufügen:
   - `CR_API_KEY` = (der Clash Royale API Key aus config.json)
   - `CR_FINDER_PASSWORD` = `CRFinder2024!` (oder eigenes wählen)
   - `FLASK_ENV` = `production`
5. "Create Web Service"

### Wichtige Hinweise:
- Render Free Tier schläft nach 15 Min Inaktivität ein (Cold Start ~30s)
- API nutzt RoyaleAPI Proxy (IP `45.79.218.79` muss bei Supercell whitelisted sein)
- Render MCP kann keine Free-Tier Services erstellen (muss manuell gemacht werden)

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
| `/` | GET | Serve main page |
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
