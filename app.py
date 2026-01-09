#!/usr/bin/env python3
"""
Clash Royale Tournament Finder
A web app to find and filter CR tournaments
"""

import json
import os
import sys
import time
import threading
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from functools import wraps
import secrets
import requests as http_requests

app = Flask(__name__)

# =============================================================================
# SESSION & AUTH CONFIGURATION
# =============================================================================
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 30  # 30 days

# Password from environment (empty = no auth required)
APP_PASSWORD = os.environ.get('CR_FINDER_PASSWORD', '')


def login_required(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not APP_PASSWORD:
            return f(*args, **kwargs)  # No password = skip auth
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# =============================================================================
# LOGGING SETUP
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOGS_DIR, 'tournament_finder.log')

# Create logger
logger = logging.getLogger('TournamentFinder')
logger.setLevel(logging.DEBUG)

# File handler (rotating, max 5MB, keep 3 backups)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
file_handler.setLevel(logging.INFO)
file_format = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler.setFormatter(file_format)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_format = logging.Formatter('%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
console_handler.setFormatter(console_format)

# Add handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Search statistics (reset per search)
search_stats = {
    'queries_completed': 0,
    'drill_downs': 0,
    'rate_limits': 0,
    'api_errors': 0,
    'tournaments_by_mode': defaultdict(int)
}

# Paths
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
GAME_MODES_PATH = os.path.join(BASE_DIR, 'game_modes.json')

# API
API_BASE = "https://proxy.royaleapi.dev/v1"

# Load game modes
def load_game_modes():
    try:
        with open(GAME_MODES_PATH, 'r') as f:
            return json.load(f)
    except:
        return {}

GAME_MODES = load_game_modes()


def load_config():
    """Load config from file"""
    default = {
        "api_key": "",
        "filters": {
            "game_modes": [],
            "tournament_type": "open",
            "status": "all",
            "min_players": 0,
            "max_players": None,
            "level_caps": [],
            "min_remaining_minutes": 0
        }
    }
    try:
        with open(CONFIG_PATH, 'r') as f:
            saved = json.load(f)
            # Merge with defaults
            default.update(saved)
            if "filters" in saved:
                default["filters"].update(saved["filters"])
            return default
    except:
        return default


def save_config(config):
    """Save config to file"""
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


def get_api_headers():
    """Get API headers with auth - env var takes precedence over config.json"""
    api_key = os.environ.get('CR_API_KEY')
    if not api_key:
        config = load_config()
        api_key = config.get('api_key', '')
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }


def has_api_key():
    """Check if an API key is configured (env var or config)"""
    if os.environ.get('CR_API_KEY'):
        return True
    config = load_config()
    return bool(config.get('api_key'))


def fetch_tournaments_by_query(query):
    """Fetch tournaments matching a query string with retry on failure"""
    global search_stats
    for attempt in range(2):
        try:
            resp = http_requests.get(
                f"{API_BASE}/tournaments",
                headers=get_api_headers(),
                params={"name": query, "limit": 100},
                timeout=10
            )
            if resp.status_code == 200:
                search_stats['queries_completed'] += 1
                return resp.json().get("items", [])
            elif resp.status_code == 429:
                search_stats['rate_limits'] += 1
                time.sleep(0.5 + attempt)
                continue
            else:
                search_stats['api_errors'] += 1
                logger.debug(f"API error for query '{query}': HTTP {resp.status_code}")
        except Exception as e:
            search_stats['api_errors'] += 1
            logger.debug(f"Request error for query '{query}': {e}")
    return []


def fetch_tournament_detail(tag):
    """Fetch detailed info for a single tournament by tag.

    The detail API returns startedTime (actual start) and endedTime.

    Args:
        tag: Tournament tag (e.g., "#ABC123")

    Returns:
        dict with tournament details, or None if fetch failed
    """
    encoded_tag = quote(tag, safe='')

    for attempt in range(2):
        try:
            resp = http_requests.get(
                f"{API_BASE}/tournaments/{encoded_tag}",
                headers=get_api_headers(),
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(0.5 + attempt)
                continue
        except:
            pass
    return None


def fetch_tournament_details_batch(tournaments):
    """Fetch details for multiple tournaments in parallel.

    Gets accurate startedTime/endedTime from detail API.

    Args:
        tournaments: List of tournament dicts (must have 'tag' field)

    Returns:
        Same list with startedTime/endedTime added where available
    """
    if not tournaments:
        return tournaments

    tags = [t['tag'] for t in tournaments]

    with ThreadPoolExecutor(max_workers=min(40, len(tags))) as executor:
        details = list(executor.map(fetch_tournament_detail, tags))

    for tournament, detail in zip(tournaments, details):
        if detail:
            if 'startedTime' in detail:
                tournament['startedTime'] = detail['startedTime']
            if 'endedTime' in detail:
                tournament['endedTime'] = detail['endedTime']

    return tournaments


def fetch_all_tournaments():
    """Fetch tournaments with best coverage in reasonable time (~20-30s).

    Strategy:
    - All 2-letter combos (676) for broad coverage
    - Common words in multiple languages for specific matches
    - Drill down when hitting API limit (20 results)
    """
    global search_stats

    # Reset stats for this search
    search_stats = {
        'queries_completed': 0,
        'drill_downs': 0,
        'rate_limits': 0,
        'api_errors': 0,
        'tournaments_by_mode': defaultdict(int)
    }

    logger.info("Starting tournament fetch...")
    start_time = time.time()

    letters = 'abcdefghijklmnopqrstuvwxyz'
    chars = list(letters + '0123456789')

    # 2-letter combinations (676)
    queries = [a + b for a in letters for b in letters]

    # Single digits
    queries.extend(list('0123456789'))

    # Common tournament words (improves non-monotonic coverage)
    common_words = [
        'torneo', 'tornei', 'tourno', 'turnie', 'free', 'open', 'join',
        'clan', 'war', 'pro', 'noob', 'legend', 'champ', 'master', 'elite',
        'draft', 'mega', 'super', 'test', 'fun', '1000', '500', '100'
    ]
    queries.extend(common_words)

    all_tournaments = {}
    searched = set()
    to_search = list(queries)

    WORKERS = 40

    while to_search:
        # Dedupe
        batch = [q for q in to_search if q not in searched]
        for q in batch:
            searched.add(q)

        if not batch:
            break

        to_search = []

        # Search in parallel
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            results = list(executor.map(fetch_tournaments_by_query, batch))

        # Process and drill down if needed
        for query, result_list in zip(batch, results):
            for t in result_list:
                all_tournaments[t['tag']] = t

            # Drill down if we hit the limit, up to 4 chars max
            if len(result_list) >= 20 and len(query) < 4:
                search_stats['drill_downs'] += 1
                for c in chars:
                    new_q = query + c
                    if new_q not in searched:
                        to_search.append(new_q)

    elapsed = time.time() - start_time

    # Count tournaments by game mode
    for t in all_tournaments.values():
        mode_id = str(t.get('gameMode', {}).get('id', 'unknown'))
        search_stats['tournaments_by_mode'][mode_id] += 1

    logger.info(f"Fetch completed in {elapsed:.1f}s")
    logger.info(f"Queries: {search_stats['queries_completed']}, Drill-downs: {search_stats['drill_downs']}, Rate-limits: {search_stats['rate_limits']}, Errors: {search_stats['api_errors']}")
    logger.info(f"Found {len(all_tournaments)} unique tournaments")

    # Log breakdown by game mode
    logger.info("Game Mode breakdown:")
    for mode_id, count in sorted(search_stats['tournaments_by_mode'].items(), key=lambda x: -x[1]):
        mode_name = GAME_MODES.get(mode_id, f"Unknown ({mode_id})")
        logger.info(f"  - {mode_name}: {count}")

    return list(all_tournaments.values())


def parse_cr_time(time_str):
    """Parse CR API time format: 20260105T220549.000Z"""
    try:
        return datetime.strptime(time_str, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
    except:
        return None


def calc_remaining_minutes(tournament):
    """Calculate remaining minutes for a tournament.

    Uses startedTime if available (from detail API), otherwise
    falls back to estimated start time (createdTime + preparationDuration).
    """
    now = datetime.now(timezone.utc)
    status = tournament.get('status')
    duration = tournament.get('duration', 0)

    # Ended tournaments have 0 remaining
    if status == 'ended':
        return 0

    # For inProgress tournaments, use actual startedTime if available
    if status == 'inProgress':
        started = parse_cr_time(tournament.get('startedTime'))
        if started:
            end_time = started.timestamp() + duration
            remaining_sec = end_time - now.timestamp()
            return max(0, int(remaining_sec / 60))

    # Fallback: use estimated time (createdTime + preparationDuration)
    created = parse_cr_time(tournament.get('createdTime'))
    if not created:
        return None

    prep_duration = tournament.get('preparationDuration', 0)
    estimated_start = created.timestamp() + prep_duration
    end_time = estimated_start + duration

    remaining_sec = end_time - now.timestamp()
    return max(0, int(remaining_sec / 60))


def calc_elapsed_minutes(tournament):
    """Calculate elapsed minutes since tournament started.

    Uses startedTime if available (from detail API), otherwise
    falls back to estimated start time.
    """
    if tournament.get('status') != 'inProgress':
        return None

    now = datetime.now(timezone.utc)

    # Prefer actual startedTime from detail API
    started = parse_cr_time(tournament.get('startedTime'))
    if started:
        elapsed_sec = now.timestamp() - started.timestamp()
        return max(0, int(elapsed_sec / 60))

    # Fallback: use estimated start time
    created = parse_cr_time(tournament.get('createdTime'))
    if not created:
        return None

    prep_duration = tournament.get('preparationDuration', 0)
    estimated_start = created.timestamp() + prep_duration
    elapsed_sec = now.timestamp() - estimated_start

    return max(0, int(elapsed_sec / 60))


def get_mode_name(mode_id):
    """Get human-readable game mode name"""
    return GAME_MODES.get(str(mode_id), f"Unknown ({mode_id})")


def filter_tournaments(tournaments, filters, apply_time_filter=True):
    """Apply filters to tournament list.

    Args:
        tournaments: List of tournament dicts
        filters: Dict of filter criteria
        apply_time_filter: If False, skip time-based filtering (for before detail fetch)
    """
    filtered = []

    for t in tournaments:
        # Tournament type filter
        t_type = filters.get('tournament_type', 'all')
        if t_type != 'all':
            if t_type == 'open' and t.get('type') != 'open':
                continue
            if t_type == 'password' and t.get('type') != 'passwordProtected':
                continue

        # Status filter
        status = filters.get('status', 'all')
        if status != 'all':
            if status == 'inProgress' and t.get('status') != 'inProgress':
                continue
            if status == 'inPreparation' and t.get('status') != 'inPreparation':
                continue

        # Game mode filter
        game_modes = filters.get('game_modes', [])
        if game_modes:
            mode_id = str(t.get('gameMode', {}).get('id', ''))
            if mode_id not in game_modes:
                continue

        # Level cap filter
        level_caps = filters.get('level_caps', [])
        if level_caps:
            if str(t.get('levelCap', '')) not in level_caps:
                continue

        # Player count filters
        current_players = t.get('capacity', 0)
        min_players = filters.get('min_players', 0) or 0
        max_players = filters.get('max_players')

        if current_players < min_players:
            continue
        if max_players and current_players > max_players:
            continue

        # Time filters and computed fields (only if apply_time_filter is True)
        if apply_time_filter:
            remaining = calc_remaining_minutes(t)
            min_remaining = filters.get('min_remaining_minutes', 0) or 0
            max_remaining = filters.get('max_remaining_minutes')

            if remaining is not None:
                if remaining < min_remaining:
                    continue
                if max_remaining and remaining > max_remaining:
                    continue

            # Add computed fields
            t['_remaining_minutes'] = remaining
            t['_elapsed_minutes'] = calc_elapsed_minutes(t)

        t['_mode_name'] = get_mode_name(t.get('gameMode', {}).get('id'))
        filtered.append(t)

    # Sort by remaining time (only if we have time data)
    if apply_time_filter:
        filtered.sort(key=lambda x: (
            x.get('_remaining_minutes') is None,
            x.get('_remaining_minutes') or 9999,
            -x.get('capacity', 0)
        ))

    return filtered


# Routes

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    # If no password configured, redirect to main page
    if not APP_PASSWORD:
        return redirect(url_for('index'))

    # Already logged in
    if session.get('authenticated'):
        return redirect(url_for('index'))

    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if secrets.compare_digest(password, APP_PASSWORD):
            session.permanent = True
            session['authenticated'] = True
            return redirect(url_for('index'))
        error = 'Falsches Passwort'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """Serve main page"""
    return render_template('index.html')


@app.route('/api/tournaments')
@login_required
def api_tournaments():
    """Fetch and filter tournaments with accurate timing from detail API"""
    if not has_api_key():
        return jsonify({"error": "API key not configured"}), 400

    # Get filters from query params or use saved defaults
    filters = {
        "game_modes": request.args.getlist('game_modes') or [],
        "tournament_type": request.args.get('tournament_type', 'open'),
        "status": request.args.get('status', 'all'),
        "min_players": int(request.args.get('min_players', 0) or 0),
        "max_players": int(request.args.get('max_players', 0) or 0) or None,
        "level_caps": request.args.getlist('level_caps') or [],
        "min_remaining_minutes": int(request.args.get('min_remaining_minutes', 0) or 0),
        "max_remaining_minutes": int(request.args.get('max_remaining_minutes', 0) or 0) or None
    }

    # Log the search
    logger.info("=" * 60)
    logger.info("=== NEW SEARCH ===")
    logger.info(f"Filters: game_modes={filters['game_modes']}, status={filters['status']}, type={filters['tournament_type']}")
    logger.info(f"         level_caps={filters['level_caps']}, players={filters['min_players']}-{filters['max_players']}")
    logger.info(f"         remaining_minutes={filters['min_remaining_minutes']}-{filters['max_remaining_minutes']}")

    # Phase 1: Fetch all tournaments (search API)
    tournaments = fetch_all_tournaments()
    unfiltered_total = len(tournaments)

    # Phase 2: Apply non-time filters first (reduces to ~10-50 tournaments)
    filtered = filter_tournaments(tournaments, filters, apply_time_filter=False)
    logger.info(f"After non-time filters: {len(filtered)} tournaments")

    # Phase 3: Fetch details for filtered tournaments (gets accurate startedTime)
    filtered = fetch_tournament_details_batch(filtered)

    # Phase 4: Apply time filters with accurate times
    filtered = filter_tournaments(filtered, filters, apply_time_filter=True)
    logger.info(f"After time filters: {len(filtered)} tournaments")

    # Log all matching tournaments
    if filtered:
        logger.info("Matching tournaments:")
        for t in filtered:
            mode_name = t.get('_mode_name', 'Unknown')
            logger.info(f"  {t.get('tag')} \"{t.get('name')}\" [{mode_name}] {t.get('capacity')}/{t.get('maxCapacity')} players")

    # Prepare response
    result = []
    for t in filtered:
        result.append({
            "tag": t.get('tag'),
            "name": t.get('name'),
            "type": t.get('type'),
            "status": t.get('status'),
            "players": t.get('capacity', 0),
            "maxPlayers": t.get('maxCapacity', 0),
            "levelCap": t.get('levelCap'),
            "gameMode": t.get('_mode_name'),
            "gameModeId": t.get('gameMode', {}).get('id'),
            "remainingMinutes": t.get('_remaining_minutes'),
            "elapsedMinutes": t.get('_elapsed_minutes')
        })

    logger.info(f"=== SEARCH COMPLETE: {len(result)} results ===")

    return jsonify({
        "tournaments": result,
        "total": len(result),
        "unfilteredTotal": unfiltered_total,
        "stats": {
            "queries": search_stats['queries_completed'],
            "drillDowns": search_stats['drill_downs'],
            "rateLimits": search_stats['rate_limits'],
            "apiErrors": search_stats['api_errors'],
            "tournamentsByMode": dict(search_stats['tournaments_by_mode'])
        }
    })


@app.route('/api/game-modes')
@login_required
def api_game_modes():
    """Return game mode mapping"""
    # Only return modes that are commonly used in tournaments
    common_modes = {
        "72000001": "Double Elixir",
        "72000005": "Draft",
        "72000009": "Normal",
        "72000013": "Double Elixir Draft",
        "72000024": "Sudden Death",
        "72000027": "Triple Elixir",
        "72000042": "Mega Draft",
        "72000194": "Triple Draft"
    }
    return jsonify(common_modes)


@app.route('/api/logs')
@login_required
def api_logs():
    """Get log file contents for debugging.

    Query params:
        lines: Number of lines to return (default 500)
        search: Search for specific text (e.g., tournament tag)
    """
    lines_count = int(request.args.get('lines', 500))
    search_term = request.args.get('search', '').strip()

    try:
        if not os.path.exists(LOG_FILE):
            return jsonify({"logs": [], "message": "No log file yet"})

        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()

        # If searching, filter lines
        if search_term:
            matching_lines = [line for line in all_lines if search_term.lower() in line.lower()]
            return jsonify({
                "logs": matching_lines[-lines_count:],
                "total_matches": len(matching_lines),
                "search_term": search_term
            })

        # Return last N lines
        return jsonify({
            "logs": all_lines[-lines_count:],
            "total_lines": len(all_lines)
        })
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/config', methods=['GET'])
@login_required
def api_get_config():
    """Get saved config"""
    config = load_config()

    # Check for API key: env var takes precedence
    env_api_key = os.environ.get('CR_API_KEY', '')
    config_api_key = config.get('api_key', '')
    api_key = env_api_key or config_api_key
    api_key_from_env = bool(env_api_key)

    # Mask the API key for display (show first 10 and last 6 chars)
    masked_key = None
    if api_key and len(api_key) > 20:
        masked_key = api_key[:10] + '...' + api_key[-6:]

    return jsonify({
        "has_api_key": bool(api_key),
        "api_key_from_env": api_key_from_env,
        "masked_key": masked_key,
        "filters": config.get('filters', {})
    })


@app.route('/api/config', methods=['POST'])
@login_required
def api_save_config():
    """Save config"""
    data = request.json
    config = load_config()

    if 'api_key' in data:
        config['api_key'] = data['api_key']

    if 'filters' in data:
        config['filters'] = data['filters']

    save_config(config)
    return jsonify({"success": True})


@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    """Heartbeat endpoint to track browser connection"""
    global last_heartbeat
    last_heartbeat = time.time()
    return jsonify({"status": "ok"})


@app.route('/api/shutdown', methods=['POST'])
def api_shutdown():
    """Shutdown the server"""
    def shutdown():
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=shutdown).start()
    return jsonify({"status": "shutting down"})


if __name__ == '__main__':
    print("=" * 50)
    print("🏆 Clash Royale Tournament Finder")
    print("=" * 50)

    config = load_config()
    if not config.get('api_key'):
        print("\n⚠️  No API key configured!")
        print("Enter your API key in the web interface.\n")

    print("Starting server at http://localhost:5050")
    print("Press Ctrl+C to stop\n")

    app.run(host='127.0.0.1', port=5050, debug=False)
