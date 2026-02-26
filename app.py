#!/usr/bin/env python3
"""
Clash Royale Tournament Finder
A web app to find and filter CR tournaments
"""

import json
import os
import time
import threading
import logging
import queue
import asyncio
import aiohttp
import certifi
import ssl
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from collections import defaultdict
from urllib.parse import quote
from flask import Flask, render_template, jsonify, request, session, redirect, url_for, send_from_directory, Response, stream_with_context
from functools import wraps
import secrets

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

# Avoid duplicated log lines if the module gets imported multiple times.
if not logger.handlers:
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

# =============================================================================
# =============================================================================
# ASYNC API CLIENT CONFIGURATION
# =============================================================================

# Search statistics (reset per search)
search_stats = {
    'queries_completed': 0,
    'drill_downs': 0,
    'rate_limits': 0,
    'api_errors': 0,
    'tournaments_by_mode': defaultdict(int)
}

# Cached crawler results (search API) to avoid repeated heavy crawls across requests.
_SEARCH_CACHE_COND = threading.Condition()
_SEARCH_CACHE = None
_SEARCH_FETCH_IN_PROGRESS = False

# Paths
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')
GAME_MODES_PATH = os.path.join(BASE_DIR, 'game_modes.json')

# API

def get_ssl_context():
    return ssl.create_default_context(cafile=certifi.where())

API_TIMEOUT = aiohttp.ClientTimeout(total=10)

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


async def validate_api_access_async():
    """Validate API key by making a cheap request asynchronously."""
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=get_ssl_context())) as session:
        try:
            async with session.get(
                f"{API_BASE}/tournaments",
                headers=get_api_headers(),
                params={"name": "a", "limit": 1},
                timeout=API_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, None
                if resp.status in (401, 403):
                    return False, "Unauthorized (API key invalid or not accepted by proxy)."
                if resp.status == 429:
                    return False, "Rate limited by API (429). Try again in a moment."
                return False, f"API error: HTTP {resp.status}"
        except Exception as e:
            return False, f"API request failed: {e}"

def validate_api_access():
    return asyncio.run(validate_api_access_async())

async def fetch_tournaments_by_query_async(session, query):
    """Fetch tournaments matching a query string asynchronously with retry on failure"""
    global search_stats
    for attempt in range(2):
        try:
            async with session.get(
                f"{API_BASE}/tournaments",
                headers=get_api_headers(),
                params={"name": query, "limit": 100},
                timeout=API_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    search_stats['queries_completed'] += 1
                    return data.get("items", [])
                elif resp.status == 429:
                    search_stats['rate_limits'] += 1
                    await asyncio.sleep(0.5 + attempt)
                    continue
                else:
                    search_stats['api_errors'] += 1
                    logger.debug(f"API error for query '{query}': HTTP {resp.status}")
        except Exception as e:
            search_stats['api_errors'] += 1
            logger.debug(f"Request error for query '{query}': {e}")
    return []


async def fetch_tournament_detail_async(session, tag):
    """Fetch detailed info for a single tournament by tag asynchronously."""
    encoded_tag = quote(tag, safe='')
    for attempt in range(2):
        try:
            async with session.get(
                f"{API_BASE}/tournaments/{encoded_tag}",
                headers=get_api_headers(),
                timeout=API_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 429:
                    await asyncio.sleep(0.5 + attempt)
                    continue
        except:
            pass
    return None


_DETAIL_CACHE_LOCK = threading.Lock()
_DETAIL_CACHE = {}


async def _get_cached_tournament_detail_async(session, tag):
    ttl = int(os.environ.get("DETAIL_CACHE_TTL_SECONDS", 300))
    now = time.time()

    with _DETAIL_CACHE_LOCK:
        cached = _DETAIL_CACHE.get(tag)
        if cached and now < cached["expires_at"]:
            return cached["detail"]

    detail = await fetch_tournament_detail_async(session, tag)
    if detail:
        with _DETAIL_CACHE_LOCK:
            _DETAIL_CACHE[tag] = {"expires_at": time.time() + ttl, "detail": detail}
    return detail


async def fetch_tournament_details_batch_async(tournaments, progress_cb=None, stop_event=None):
    if not tournaments:
        return tournaments

    by_tag = {t['tag']: t for t in tournaments if t.get('tag')}
    tags = list(by_tag.keys())

    total = len(tags)
    completed = 0
    last_emit_ts = 0.0

    def emit(force=False):
        nonlocal last_emit_ts
        if not progress_cb:
            return
        now_ts = time.time()
        if not force and (now_ts - last_emit_ts) < 0.25:
            return
        last_emit_ts = now_ts
        progress_cb({
            "phase": "details",
            "completed": completed,
            "total": total,
        })

    emit(force=True)

    max_detail_workers = int(os.environ.get('DETAIL_WORKERS', 100))

    async def fetch_and_update(tag, session, sem):
        nonlocal completed
        if stop_event and stop_event.is_set():
            return
        async with sem:
            detail = await _get_cached_tournament_detail_async(session, tag)

        tournament = by_tag.get(tag)
        if tournament and detail:
            if 'startedTime' in detail:
                tournament['startedTime'] = detail['startedTime']
            if 'endedTime' in detail:
                tournament['endedTime'] = detail['endedTime']

        completed += 1
        emit()

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=get_ssl_context())) as session:
        sem = asyncio.Semaphore(max_detail_workers)
        tasks = [fetch_and_update(tag, session, sem) for tag in tags]
        await asyncio.gather(*tasks)

    emit(force=True)
    return tournaments

def fetch_tournament_details_batch(tournaments, progress_cb=None, stop_event=None):
    if not tournaments:
        return tournaments
    return asyncio.run(fetch_tournament_details_batch_async(tournaments, progress_cb, stop_event))

def fetch_tournament_details_for_in_progress(tournaments, progress_cb=None, stop_event=None):
    """Fetch details only for in-progress tournaments (startedTime matters there)."""
    if not tournaments:
        return tournaments
    subset = [t for t in tournaments if t.get('status') == 'inProgress']
    if subset:
        fetch_tournament_details_batch(subset, progress_cb=progress_cb, stop_event=stop_event)
    return tournaments


def _snapshot_search_stats():
    # Make the defaultdict JSON-friendly and avoid accidental mutation.
    return {
        "queries_completed": int(search_stats.get("queries_completed", 0)),
        "drill_downs": int(search_stats.get("drill_downs", 0)),
        "rate_limits": int(search_stats.get("rate_limits", 0)),
        "api_errors": int(search_stats.get("api_errors", 0)),
        "tournaments_by_mode": dict(search_stats.get("tournaments_by_mode", {})),
    }


def get_fresh_search_cache():
    """Return the cached crawler result if it's still fresh, else None."""
    ttl = int(os.environ.get("SEARCH_CACHE_TTL_SECONDS", 60))
    if ttl <= 0:
        return None
    with _SEARCH_CACHE_COND:
        now = time.time()
        if _SEARCH_CACHE and now < _SEARCH_CACHE["expires_at_ts"]:
            return _SEARCH_CACHE
    return None


def get_cached_search_results(force_refresh=False):
    """Fetch tournaments via the crawler, with an in-memory TTL cache and in-flight dedupe."""
    global _SEARCH_CACHE, _SEARCH_FETCH_IN_PROGRESS

    ttl = int(os.environ.get("SEARCH_CACHE_TTL_SECONDS", 60))  # 0 disables cache reuse (but still dedupes in-flight)

    with _SEARCH_CACHE_COND:
        now = time.time()
        if not force_refresh and ttl > 0 and _SEARCH_CACHE and now < _SEARCH_CACHE["expires_at_ts"]:
            return _SEARCH_CACHE

        # If a fetch is already running, wait for it and reuse its result (even if ttl==0).
        if _SEARCH_FETCH_IN_PROGRESS:
            while _SEARCH_FETCH_IN_PROGRESS:
                _SEARCH_CACHE_COND.wait(timeout=0.5)
            if _SEARCH_CACHE:
                return _SEARCH_CACHE

        _SEARCH_FETCH_IN_PROGRESS = True

    # Do the expensive work outside the lock.
    cache = None
    try:
        tournaments = fetch_all_tournaments()
        fetched_at_ts = time.time()
        fetched_at_iso = datetime.now(timezone.utc).isoformat()
        stats_snapshot = _snapshot_search_stats()

        cache = {
            "tournaments": tournaments,
            "fetchedAt": fetched_at_iso,
            "fetched_at_ts": fetched_at_ts,
            "expires_at_ts": fetched_at_ts + ttl if ttl > 0 else fetched_at_ts,
            "stats": stats_snapshot,
        }
        with _SEARCH_CACHE_COND:
            _SEARCH_CACHE = cache
        return cache
    finally:
        with _SEARCH_CACHE_COND:
            _SEARCH_FETCH_IN_PROGRESS = False
            _SEARCH_CACHE_COND.notify_all()


async def fetch_all_tournaments_async(progress_cb=None, stop_event=None):
    global search_stats

    # Reset stats for this search
    search_stats = {
        'queries_completed': 0,
        'drill_downs': 0,
        'rate_limits': 0,
        'api_errors': 0,
        'tournaments_by_mode': defaultdict(int)
    }

    logger.info("Starting tournament fetch (asyncio)...")
    start_time = time.time()

    letters = 'abcdefghijklmnopqrstuvwxyz'
    digits = '0123456789'
    latin_chars = list(letters + digits)

    # 2-letter combinations (676)
    queries = [a + b for a in letters for b in letters]

    # Single digits
    queries.extend(list(digits))

    # Cyrillic letters (33) for Russian tournament names
    cyrillic_letters = 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
    queries.extend(list(cyrillic_letters))

    # Common tournament words
    common_words = [
        'torneo', 'tornei', 'tourno', 'turnie', 'free', 'open', 'join',
        'clan', 'war', 'pro', 'noob', 'legend', 'champ', 'master', 'elite',
        'draft', 'mega', 'super', 'test', 'fun', '1000', '500', '100'
    ]
    queries.extend(common_words)

    all_tournaments = {}
    searched = set()

    # Increase workers since async I/O is very lightweight
    WORKERS = int(os.environ.get('SEARCH_WORKERS', 100))
    max_latin_len = int(os.environ.get("MAX_QUERY_LEN", 4))
    max_cyrillic_len = int(os.environ.get("MAX_CYRILLIC_QUERY_LEN", 2))

    def drilldown_chars_and_limit(q):
        if any(ch in cyrillic_letters for ch in q):
            return list(cyrillic_letters + digits), max_cyrillic_len
        return latin_chars, max_latin_len

    q = asyncio.Queue()
    for query in queries:
        q.put_nowait(query)

    scheduled = q.qsize()
    completed = 0
    last_emit_ts = 0.0

    def emit(force=False, message=None):
        nonlocal last_emit_ts
        if not progress_cb:
            return
        now_ts = time.time()
        if not force and (now_ts - last_emit_ts) < 0.25:
            return
        last_emit_ts = now_ts

        pending = (scheduled - completed)
        payload = {
            "phase": "crawl",
            "completed": completed,
            "pending": pending,
            "uniqueFound": len(all_tournaments),
            "drillDowns": search_stats.get('drill_downs', 0),
            "rateLimits": search_stats.get('rate_limits', 0),
            "apiErrors": search_stats.get('api_errors', 0),
        }
        if message:
            payload["message"] = message
        progress_cb(payload)

    async def worker(session, sem):
        nonlocal completed, scheduled
        while True:
            try:
                query = await q.get()
            except asyncio.CancelledError:
                break

            if stop_event and stop_event.is_set():
                q.task_done()
                continue

            if query in searched:
                q.task_done()
                continue
            searched.add(query)

            async with sem:
                results = await fetch_tournaments_by_query_async(session, query)

            for t in results:
                all_tournaments[t['tag']] = t

            chars, max_len = drilldown_chars_and_limit(query)
            if len(results) >= 20 and len(query) < max_len:
                search_stats['drill_downs'] += 1
                for c in chars:
                    new_q = query + c
                    if new_q not in searched:
                        q.put_nowait(new_q)
                        scheduled += 1

            completed += 1
            emit()
            q.task_done()

    emit(force=True)

    connector = aiohttp.TCPConnector(limit=WORKERS, ssl=get_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(WORKERS)
        workers = [asyncio.create_task(worker(session, sem)) for _ in range(WORKERS)]
        await q.join()
        for w in workers:
            w.cancel()

    emit(force=True)

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

def fetch_all_tournaments(progress_cb=None, stop_event=None):
    return asyncio.run(fetch_all_tournaments_async(progress_cb, stop_event))

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


@app.route('/manifest.json')
def manifest():
    """Serve PWA manifest"""
    return send_from_directory(BASE_DIR, 'manifest.json', mimetype='application/manifest+json')


@app.route('/api/tournaments')
@login_required
def api_tournaments():
    """Fetch and filter tournaments with accurate timing from detail API"""
    if not has_api_key():
        return jsonify({"error": "API key not configured"}), 400
    cache = get_fresh_search_cache()
    if cache is None:
        ok, err = validate_api_access()
        if not ok:
            return jsonify({"error": err}), 400

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

    # Phase 1: Fetch all tournaments (crawler), cached across requests
    if cache is None:
        cache = get_cached_search_results(force_refresh=False)
    tournaments = cache["tournaments"]
    unfiltered_total = len(tournaments)
    cached_stats = cache.get("stats", _snapshot_search_stats())

    # Phase 2: Apply non-time filters first (reduces to ~10-50 tournaments)
    filtered = filter_tournaments(tournaments, filters, apply_time_filter=False)
    logger.info(f"After non-time filters: {len(filtered)} tournaments")

    # Phase 3: Fetch details only when needed (in-progress tournaments)
    filtered = fetch_tournament_details_for_in_progress(filtered)

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
            "queries": cached_stats.get("queries_completed", 0),
            "drillDowns": cached_stats.get("drill_downs", 0),
            "rateLimits": cached_stats.get("rate_limits", 0),
            "apiErrors": cached_stats.get("api_errors", 0),
            "tournamentsByMode": cached_stats.get("tournaments_by_mode", {})
        }
    })


@app.route('/api/tournaments/search')
@login_required
def api_tournaments_search():
    """Fetch ALL tournaments without filtering - for client-side filtering.

    Returns all tournament data with raw time fields so the client can:
    1. Cache the results
    2. Apply filters instantly without new API calls
    """
    if not has_api_key():
        return jsonify({"error": "API key not configured"}), 400
    force_refresh = request.args.get("force", "").strip() in ("1", "true", "yes")
    cache = None
    if not force_refresh:
        cache = get_fresh_search_cache()
    if cache is None:
        ok, err = validate_api_access()
        if not ok:
            return jsonify({"error": err}), 400

    logger.info("=" * 60)
    logger.info("=== FETCH ALL TOURNAMENTS (for client-side filtering) ===")

    # Phase 1: Fetch all tournaments (crawler), cached across requests
    if cache is None:
        cache = get_cached_search_results(force_refresh=force_refresh)
    tournaments = cache["tournaments"]
    total_count = len(tournaments)
    cached_stats = cache.get("stats", _snapshot_search_stats())

    # Phase 2: Fetch details only for in-progress tournaments (startedTime matters there)
    logger.info(f"Fetching details for in-progress tournaments (from {total_count} total)...")
    tournaments = fetch_tournament_details_for_in_progress(tournaments)

    # Prepare response with raw time fields
    result = []
    for t in tournaments:
        result.append({
            "tag": t.get('tag'),
            "name": t.get('name'),
            "type": t.get('type'),
            "status": t.get('status'),
            "players": t.get('capacity', 0),
            "maxPlayers": t.get('maxCapacity', 0),
            "levelCap": t.get('levelCap'),
            "gameModeId": str(t.get('gameMode', {}).get('id', '')),
            "gameModeName": get_mode_name(t.get('gameMode', {}).get('id')),
            # Raw time fields for client-side calculation
            "createdTime": t.get('createdTime'),
            "preparationDuration": t.get('preparationDuration', 0),
            "duration": t.get('duration', 0),
            "startedTime": t.get('startedTime'),
            "endedTime": t.get('endedTime')
        })

    logger.info(f"=== FETCH COMPLETE: {len(result)} tournaments ===")

    return jsonify({
        "tournaments": result,
        "total": len(result),
        "fetchedAt": cache.get("fetchedAt") or datetime.now(timezone.utc).isoformat(),
        "stats": {
            "queries": cached_stats.get("queries_completed", 0),
            "drillDowns": cached_stats.get("drill_downs", 0),
            "rateLimits": cached_stats.get("rate_limits", 0),
            "apiErrors": cached_stats.get("api_errors", 0),
            "tournamentsByMode": cached_stats.get("tournaments_by_mode", {})
        }
    })


def build_tournaments_search_payload(tournaments, fetched_at_iso, stats_snapshot):
    """Build the `/api/tournaments/search` response payload from raw tournaments."""
    result = []
    for t in tournaments:
        result.append({
            "tag": t.get('tag'),
            "name": t.get('name'),
            "type": t.get('type'),
            "status": t.get('status'),
            "players": t.get('capacity', 0),
            "maxPlayers": t.get('maxCapacity', 0),
            "levelCap": t.get('levelCap'),
            "gameModeId": str(t.get('gameMode', {}).get('id', '')),
            "gameModeName": get_mode_name(t.get('gameMode', {}).get('id')),
            # Raw time fields for client-side calculation
            "createdTime": t.get('createdTime'),
            "preparationDuration": t.get('preparationDuration', 0),
            "duration": t.get('duration', 0),
            "startedTime": t.get('startedTime'),
            "endedTime": t.get('endedTime')
        })

    return {
        "tournaments": result,
        "total": len(result),
        "fetchedAt": fetched_at_iso,
        "stats": {
            "queries": stats_snapshot.get("queries_completed", 0),
            "drillDowns": stats_snapshot.get("drill_downs", 0),
            "rateLimits": stats_snapshot.get("rate_limits", 0),
            "apiErrors": stats_snapshot.get("api_errors", 0),
            "tournamentsByMode": stats_snapshot.get("tournaments_by_mode", {}),
        },
    }


@app.route('/api/tournaments/search/stream')
@login_required
def api_tournaments_search_stream():
    """Stream tournament search progress via Server-Sent Events (SSE).

    Event types:
      - progress: progress payload (phase, counts, etc.)
      - done: final payload (same shape as /api/tournaments/search)
      - fail: error payload
    """
    if not has_api_key():
        payload = f"event: fail\ndata: {json.dumps({'error': 'API key not configured'})}\n\n"
        return Response(payload, mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})

    force_refresh = request.args.get("force", "").strip() in ("1", "true", "yes")

    q = queue.Queue()
    stop_event = threading.Event()

    def push_event(event_type, data):
        try:
            msg = json.dumps(data, separators=(",", ":"))
        except Exception:
            msg = json.dumps({"error": "Failed to encode event payload"})
        q.put(f"event: {event_type}\ndata: {msg}\n\n")

    def progress_cb(payload):
        push_event("progress", payload)

    def worker():
        try:
            cache = None
            if not force_refresh:
                cache = get_fresh_search_cache()

            if cache is not None:
                tournaments = cache.get("tournaments", [])
                stats_snapshot = cache.get("stats", _snapshot_search_stats())
                fetched_at_iso = cache.get("fetchedAt") or datetime.now(timezone.utc).isoformat()
                progress_cb({"phase": "cache", "message": "Using cached crawl"})
            else:
                ok, err = validate_api_access()
                if not ok:
                    push_event("fail", {"error": err})
                    return

                tournaments = fetch_all_tournaments(progress_cb=progress_cb, stop_event=stop_event)
                stats_snapshot = _snapshot_search_stats()
                fetched_at_iso = datetime.now(timezone.utc).isoformat()

                ttl = int(os.environ.get("SEARCH_CACHE_TTL_SECONDS", 60))
                fetched_at_ts = time.time()
                new_cache = {
                    "tournaments": tournaments,
                    "fetchedAt": fetched_at_iso,
                    "fetched_at_ts": fetched_at_ts,
                    "expires_at_ts": fetched_at_ts + ttl if ttl > 0 else fetched_at_ts,
                    "stats": stats_snapshot,
                }
                with _SEARCH_CACHE_COND:
                    global _SEARCH_CACHE
                    _SEARCH_CACHE = new_cache

            # Details phase (only in-progress tournaments)
            in_progress = [t for t in tournaments if t.get('status') == 'inProgress']
            progress_cb({"phase": "details", "completed": 0, "total": len(in_progress)})
            fetch_tournament_details_batch(in_progress, progress_cb=progress_cb, stop_event=stop_event)

            progress_cb({"phase": "serialize", "message": "Preparing results"})

            payload = build_tournaments_search_payload(
                tournaments=tournaments,
                fetched_at_iso=fetched_at_iso,
                stats_snapshot=stats_snapshot,
            )
            push_event("done", payload)
        except Exception as e:
            logger.exception("SSE search failed")
            push_event("fail", {"error": str(e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        try:
            # Tell EventSource how long to wait before retrying if it reconnects.
            yield "retry: 1500\n\n"
            while True:
                try:
                    item = q.get(timeout=1.0)
                except queue.Empty:
                    # Keep-alive comment.
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield item
        finally:
            stop_event.set()

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(stream_with_context(gen()), mimetype="text/event-stream", headers=headers)


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

    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', 5050))
    print(f"Starting server at http://{host}:{port}")
    print("Press Ctrl+C to stop\n")

    app.run(host=host, port=port, debug=False)
