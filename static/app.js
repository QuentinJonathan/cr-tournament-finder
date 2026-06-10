// CR Tournament Finder — Draft Board frontend

// ---------- Service worker ----------
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/service-worker.js').catch(() => {});
}

// ==========================================================
// State
// ==========================================================
const state = {
  tournaments: [],       // raw list from /api/tournaments/search
  enriched: [],          // computed countdowns, etc. refreshed each tick
  fetchedAt: null,
  gameModes: {},         // {id: name}
  hasApiKey: false,
  apiKeyFromEnv: false,
  shutdownEnabled: true,
  isSearching: false,
  activeStream: null,
  heartbeatInterval: null,
  lastStats: null,

  // Filters / UI
  savedView: 'all',
  filters: {
    modes: new Set(),
    levelCaps: new Set(),
    minPlayers: 0,
    minMinsLeft: 0,
    maxMinsLeft: null,
    access: 'any',
  },
  search: '',
  quick: null,           // 'live' | 'prep' | null
  autoRefresh: localStorage.getItem('cr.autoRefresh') === '1',
  sort: { by: 'endsIn', dir: 'asc' },
  selectedTag: null,
  favorites: new Set(loadFavorites()),

  // Responsive
  sidebarOverlay: false,
  detailOverlay: false,
  sidebarOpen: false,
  detailOpen: false,
};

const SIDEBAR_BREAK = 1100;
const DETAIL_BREAK = 900;

function loadFavorites() {
  try {
    const raw = localStorage.getItem('cr.favorites');
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}
function persistFavorites() {
  try { localStorage.setItem('cr.favorites', JSON.stringify([...state.favorites])); } catch {}
}

// ==========================================================
// Time helpers (kept from previous implementation)
// ==========================================================
function parseCrTime(s) {
  if (!s) return null;
  try {
    const y = s.substring(0, 4), m = s.substring(4, 6), d = s.substring(6, 8);
    const hh = s.substring(9, 11), mm = s.substring(11, 13), ss = s.substring(13, 15);
    const ms = s.substring(16, 19);
    return new Date(`${y}-${m}-${d}T${hh}:${mm}:${ss}.${ms}Z`);
  } catch { return null; }
}

function computeCountdown(t) {
  // Returns {countdownType, remainingSec, totalSec}
  const now = Date.now();
  const duration = t.duration || 0;
  const prep = t.preparationDuration || 0;

  if (t.status === 'ended') {
    return { countdownType: 'ended', remainingSec: 0, totalSec: duration || 1 };
  }

  if (t.status === 'inPreparation') {
    const created = parseCrTime(t.createdTime);
    if (created) {
      const startsAt = created.getTime() + prep * 1000;
      const remaining = Math.max(0, Math.floor((startsAt - now) / 1000));
      return { countdownType: 'starts', remainingSec: remaining, totalSec: prep || 1 };
    }
    return { countdownType: 'starts', remainingSec: prep, totalSec: prep || 1 };
  }

  // inProgress
  let endsAt = null;
  const started = parseCrTime(t.startedTime);
  if (started) {
    endsAt = started.getTime() + duration * 1000;
  } else {
    const created = parseCrTime(t.createdTime);
    if (created) endsAt = created.getTime() + (prep + duration) * 1000;
  }
  if (endsAt === null) return { countdownType: 'ends', remainingSec: duration, totalSec: duration || 1 };
  const remaining = Math.max(0, Math.floor((endsAt - now) / 1000));
  return { countdownType: 'ends', remainingSec: remaining, totalSec: duration || 1 };
}

function effectiveStatusOf(t, cd) {
  // Derive the live status from the countdown so the UI doesn't go stale
  // between refreshes (prep -> live -> ended transitions happen client-side).
  if (t.status === 'ended') return 'ended';
  if (t.status === 'inPreparation') return cd.remainingSec <= 0 ? 'inProgress' : 'inPreparation';
  if (t.status === 'inProgress') return cd.remainingSec <= 0 ? 'ended' : 'inProgress';
  return t.status || 'unknown';
}

function fmtCd(sec) {
  if (sec <= 0) return '00:00';
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = n => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

function severity(remaining, total) {
  if (total <= 0) return 'ok';
  const r = remaining / total;
  if (r <= 0.15) return 'crit';
  if (r <= 0.4) return 'warn';
  return 'ok';
}

function sevColor(sev) {
  return sev === 'crit' ? 'var(--db-crit)' : sev === 'warn' ? 'var(--db-warn)' : 'var(--db-ok)';
}

function fmtAbsTime(dt) {
  if (!dt) return '—';
  const now = new Date();
  const sameDay = dt.toDateString() === now.toDateString();
  const opts = sameDay
    ? { hour: '2-digit', minute: '2-digit' }
    : { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
  return dt.toLocaleString(undefined, opts);
}

function fmtAgo(iso) {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

// ==========================================================
// DOM references (set on DOMContentLoaded)
// ==========================================================
let $ = {};
function grabRefs() {
  const id = x => document.getElementById(x);
  $ = {
    sidebar: id('sidebar'),
    sidebarClose: id('sidebar-close'),
    sidebarOpen: id('sidebar-open'),
    sidebarBackdrop: id('sidebar-backdrop'),
    settingsBtn: id('settings-btn'),
    savedViews: id('saved-views'),
    modeChecks: id('mode-checks'),
    levelCaps: id('level-caps'),
    minPlayers: id('min-players'),
    minRemaining: id('min-remaining'),
    maxRemaining: id('max-remaining'),
    accessRadio: id('access-radio'),
    clearFiltersBtn: id('clear-filters-btn'),
    saveDefaultsBtn: id('save-defaults-btn'),

    searchInput: id('search-input'),
    quickLive: id('quick-live'),
    quickPrep: id('quick-prep'),
    resultsCountText: id('results-count-text'),
    fetchAge: id('fetch-age'),
    refreshBtn: id('refresh-btn'),
    autoRefreshBtn: id('auto-refresh-btn'),

    thead: id('thead'),
    rows: id('rows'),
    empty: id('empty-state'),
    emptyTitle: id('empty-title'),
    emptySub: id('empty-sub'),

    progress: id('search-progress'),
    progressText: id('progress-text'),
    progressFill: id('progress-fill'),
    progressMetrics: id('progress-metrics'),

    detail: id('detail-panel'),
    detailBackdrop: id('detail-backdrop'),
    detailClose: id('detail-close'),
    detailTag: id('detail-tag'),
    detailCopy: id('detail-copy'),
    detailLock: id('detail-lock'),
    detailName: id('detail-name'),
    detailFav: id('detail-fav'),
    detailCountdownCard: id('detail-countdown-card'),
    detailCountdownLabel: id('detail-countdown-label'),
    detailCountdownValue: id('detail-countdown-value'),
    detailCountdownBar: id('detail-countdown-bar'),
    detailPlayers: id('detail-players'),
    detailPlayersSub: id('detail-players-sub'),
    detailLevel: id('detail-level'),
    detailMode: id('detail-mode'),
    detailAccess: id('detail-access'),
    detailAccessSub: id('detail-access-sub'),
    detailTimingLine: id('detail-timing-line'),
    detailTimingSub: id('detail-timing-sub'),
    detailJoin: id('detail-join'),
    detailFavBtn: id('detail-fav-btn'),
    detailCopyBtn: id('detail-copy-btn'),

    toast: id('toast'),

    settings: id('settings-drawer'),
    settingsClose: id('settings-close'),
    apiKeyInput: id('api-key-input'),
    saveApiKey: id('save-api-key'),
    toggleVisibility: id('toggle-visibility'),
    changeKeyBtn: id('change-key-btn'),
    keyCurrentRow: id('key-current-row'),
    keyInputRow: id('key-input-row'),
    maskedKey: id('masked-key'),
    tagSearchInput: id('tag-search-input'),
    tagSearchBtn: id('tag-search-btn'),
    tagSearchResult: id('tag-search-result'),
    refreshLogsBtn: id('refresh-logs-btn'),
    logOutput: id('log-output'),
    shutdownBtn: id('shutdown-btn'),
    modeBreakdown: id('mode-breakdown'),
    modeList: id('mode-list'),
  };
}

// ==========================================================
// Rendering — sidebar
// ==========================================================
const SAVED_VIEWS = [
  { id: 'all',       label: '📋 All tournaments' },
  { id: 'live',      label: '⚡ Live now' },
  { id: 'soon',      label: '◎ Starting soon' },
  { id: 'favorites', label: '★ Favorites' },
  { id: 'high-lvl',  label: '👑 Lvl 15+' },
];

function renderSavedViews() {
  const pool = (state.enriched && state.enriched.length ? state.enriched : state.tournaments)
    .filter(t => (t.effectiveStatus || t.status) !== 'ended');
  const counts = {
    all: pool.length,
    live: pool.filter(t => (t.effectiveStatus || t.status) === 'inProgress').length,
    soon: pool.filter(t => (t.effectiveStatus || t.status) === 'inPreparation').length,
    favorites: pool.filter(t => state.favorites.has(t.tag)).length,
    'high-lvl': pool.filter(t => (t.levelCap || 0) >= 15).length,
  };
  $.savedViews.innerHTML = '';
  SAVED_VIEWS.forEach(v => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'db-view' + (state.savedView === v.id ? ' active' : '');
    btn.innerHTML = `<span>${escapeHtml(v.label)}</span><span class="db-view-count">${counts[v.id] ?? 0}</span>`;
    btn.addEventListener('click', () => {
      state.savedView = v.id;
      renderSavedViews();
      renderRows();
      if (state.sidebarOverlay) closeSidebar();
    });
    $.savedViews.appendChild(btn);
  });
}

function renderModeChecks() {
  $.modeChecks.innerHTML = '';
  // Group mode IDs by display name — the CR API has many IDs with the same name.
  const byName = new Map();
  for (const [id, name] of Object.entries(state.gameModes || {})) {
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push(id);
  }
  const groups = [...byName.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  groups.forEach(([name, ids]) => {
    const active = ids.some(id => state.filters.modes.has(id));
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'db-mode' + (active ? ' active' : '');
    btn.innerHTML = `<span class="db-check">${active ? '✓' : ''}</span><span class="db-mode-label">${escapeHtml(name)}</span>`;
    btn.addEventListener('click', () => {
      if (active) ids.forEach(id => state.filters.modes.delete(id));
      else ids.forEach(id => state.filters.modes.add(id));
      renderModeChecks();
      renderRows();
    });
    $.modeChecks.appendChild(btn);
  });
}

function renderLevelCaps() {
  $.levelCaps.querySelectorAll('.db-lvl').forEach(btn => {
    const n = Number(btn.dataset.value);
    btn.classList.toggle('active', state.filters.levelCaps.has(n));
  });
}

// ==========================================================
// Rendering — rows / main list
// ==========================================================
function matchesFilters(t) {
  // Ended tournaments can't be joined — never show them.
  if (t.effectiveStatus === 'ended') return false;

  // Saved view
  if (state.savedView === 'live' && t.effectiveStatus !== 'inProgress') return false;
  if (state.savedView === 'soon' && t.effectiveStatus !== 'inPreparation') return false;
  if (state.savedView === 'favorites' && !state.favorites.has(t.tag)) return false;
  if (state.savedView === 'high-lvl' && (t.levelCap || 0) < 15) return false;

  // Sidebar filters
  if (state.filters.modes.size && !state.filters.modes.has(String(t.gameModeId))) return false;
  if (state.filters.levelCaps.size && !state.filters.levelCaps.has(Number(t.levelCap))) return false;
  if (state.filters.minPlayers > 0 && (t.players || 0) < state.filters.minPlayers) return false;
  const mins = (t.remainingSec || 0) / 60;
  if (state.filters.minMinsLeft > 0 && mins < state.filters.minMinsLeft) return false;
  if (state.filters.maxMinsLeft && mins > state.filters.maxMinsLeft) return false;
  if (state.filters.access === 'open' && t.type === 'passwordProtected') return false;
  if (state.filters.access === 'password' && t.type !== 'passwordProtected') return false;

  // Quick
  if (state.quick === 'live' && t.effectiveStatus !== 'inProgress') return false;
  if (state.quick === 'prep' && t.effectiveStatus !== 'inPreparation') return false;

  // Search
  const q = state.search.trim().toLowerCase();
  if (q) {
    const hay = `${t.name || ''} ${t.tag || ''} ${t.gameModeName || ''}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

function enrich() {
  state.enriched = state.tournaments.map(t => {
    const cd = computeCountdown(t);
    return {
      ...t,
      countdownType: cd.countdownType,
      remainingSec: cd.remainingSec,
      totalSec: cd.totalSec,
      effectiveStatus: effectiveStatusOf(t, cd),
    };
  });
  state.enrichedByTag = new Map(state.enriched.map(t => [t.tag, t]));
}

function currentFiltered() {
  const items = state.enriched.filter(matchesFilters);
  const dir = state.sort.dir === 'asc' ? 1 : -1;
  items.sort((a, b) => {
    switch (state.sort.by) {
      case 'name':    return (a.name || '').localeCompare(b.name || '') * dir;
      case 'mode':    return (a.gameModeName || '').localeCompare(b.gameModeName || '') * dir;
      case 'players': return ((a.players || 0) - (b.players || 0)) * dir;
      case 'level':   return ((a.levelCap || 0) - (b.levelCap || 0)) * dir;
      case 'status':  return (a.status || '').localeCompare(b.status || '') * dir;
      case 'endsIn':
      default:        return (a.remainingSec - b.remainingSec) * dir;
    }
  });
  return items;
}

function renderRows() {
  enrich();
  const items = currentFiltered();

  // Update counts and saved views (counts depend on tournaments, not filters)
  renderSavedViews();

  // Results meta
  const total = state.tournaments.length;
  $.resultsCountText.textContent = items.length === total
    ? `${items.length} results`
    : `${items.length}/${total} results`;

  // Quick pill actives
  $.quickLive.classList.toggle('active', state.quick === 'live');
  $.quickPrep.classList.toggle('active', state.quick === 'prep');

  // Sort indicators
  $.thead.querySelectorAll('.db-th').forEach(th => {
    const key = th.dataset.sort;
    th.classList.toggle('active', key === state.sort.by);
    const ind = th.querySelector('.db-sort-ind');
    if (ind) ind.textContent = key === state.sort.by ? (state.sort.dir === 'asc' ? '↑' : '↓') : '';
  });

  // Empty state
  if (items.length === 0) {
    $.rows.innerHTML = '';
    $.empty.classList.remove('hidden');
    if (total === 0 && !state.isSearching && state.hasApiKey) {
      $.emptyTitle.textContent = 'No tournaments loaded yet';
      $.emptySub.textContent = 'Click Refresh to search.';
    } else if (!state.hasApiKey) {
      $.emptyTitle.textContent = 'Set your API key to begin';
      $.emptySub.textContent = 'Open Settings and paste your developer.clashroyale.com key.';
    } else {
      $.emptyTitle.textContent = 'No tournaments match these filters';
      $.emptySub.textContent = 'Try widening level cap or clearing filters.';
    }
  } else {
    $.empty.classList.add('hidden');
    // Build rows. Using innerHTML for speed; add listeners after.
    const html = items.map(t => rowHtml(t)).join('');
    $.rows.innerHTML = html;
    attachRowListeners();
  }

  // Auto-select first row when nothing selected or selection filtered out
  if (!state.selectedTag || !items.some(t => t.tag === state.selectedTag)) {
    state.selectedTag = items[0]?.tag || null;
  }
  renderDetail();
  highlightSelectedRow();
}

function rowHtml(t) {
  const sev = severity(t.remainingSec, t.totalSec);
  const color = sevColor(sev);
  const fav = state.favorites.has(t.tag);
  const pct = Math.max(0, Math.min(100, t.maxPlayers ? (t.players / t.maxPlayers) * 100 : 0));
  const status = t.effectiveStatus || t.status;
  const live = status === 'inProgress';
  const ended = status === 'ended';
  const isPwd = t.type === 'passwordProtected';
  const cleanTag = String(t.tag || '').replace('#', '');
  const joinUrl = `https://link.clashroyale.com/en?clashroyale://joinTournament?id=${encodeURIComponent(cleanTag)}`;

  return `
    <div class="db-row" data-tag="${escapeAttr(t.tag)}">
      <div class="db-row-name-cell">
        <div class="db-row-name-line">
          <span class="db-row-star ${fav ? 'favorited' : ''}" data-fav="${escapeAttr(t.tag)}" title="${fav ? 'Unfavorite' : 'Favorite'}">★</span>
          ${isPwd ? '<span class="db-row-lock" title="Password">🔒</span>' : ''}
          <span class="db-row-name" title="${escapeAttr(t.name || '')}">${escapeHtml(t.name || '—')}</span>
        </div>
        <div class="db-row-sub"><span class="db-sub-mode">${escapeHtml(t.gameModeName || '—')} · </span>#${escapeHtml(cleanTag)}</div>
      </div>
      <div class="db-row-mode" title="${escapeAttr(t.gameModeName || '')}">${escapeHtml(t.gameModeName || '—')}</div>
      <div class="db-row-players">
        <div class="db-row-players-count">${t.players || 0}<span>/${t.maxPlayers || 0}</span></div>
        <div class="db-row-players-bar"><div class="db-row-players-bar-fill" style="width:${pct}%"></div></div>
      </div>
      <div class="db-row-level">${t.levelCap || '—'}</div>
      <div class="db-row-countdown" data-cd="${escapeAttr(t.tag)}" style="color:${color}">${fmtCd(t.remainingSec)}</div>
      <div class="db-row-status">
        <span class="db-badge ${live ? 'live' : 'soon'}">${live ? '<span class="db-badge-pulse"></span>LIVE' : ended ? '✕ ENDED' : '◎ SOON'}</span>
      </div>
      <div class="db-row-action">
        <a class="db-join-btn" href="${joinUrl}" target="_blank" rel="noopener" data-join>JOIN</a>
      </div>
    </div>
  `;
}

function attachRowListeners() {
  $.rows.querySelectorAll('.db-row').forEach(row => {
    const tag = row.dataset.tag;
    row.addEventListener('click', e => {
      const star = e.target.closest('[data-fav]');
      if (star) { toggleFavorite(star.dataset.fav); e.stopPropagation(); return; }
      const join = e.target.closest('[data-join]');
      if (join) { e.stopPropagation(); return; } // allow default link
      state.selectedTag = tag;
      if (state.detailOverlay) openDetail();
      highlightSelectedRow();
      renderDetail();
    });
  });
}

function highlightSelectedRow() {
  $.rows.querySelectorAll('.db-row').forEach(row => {
    row.classList.toggle('selected', row.dataset.tag === state.selectedTag);
  });
}

// ==========================================================
// Ticking — update countdown numbers every second
// ==========================================================
function tickCountdowns() {
  let statusChanged = false;
  state.tournaments.forEach(t => {
    const cd = computeCountdown(t);
    const newStatus = effectiveStatusOf(t, cd);
    // Update the enriched copy so filter/sort reflects live values
    const enrichedItem = state.enrichedByTag ? state.enrichedByTag.get(t.tag) : null;
    if (enrichedItem) {
      enrichedItem.remainingSec = cd.remainingSec;
      enrichedItem.countdownType = cd.countdownType;
      enrichedItem.totalSec = cd.totalSec;
      if (enrichedItem.effectiveStatus !== newStatus) {
        if (newStatus === 'inProgress' && state.favorites.has(t.tag)) notifyFavoriteLive(t);
        enrichedItem.effectiveStatus = newStatus;
        statusChanged = true;
      }
    }
    // Update row countdown cells directly
    const el = $.rows.querySelector(`[data-cd="${cssEscape(t.tag)}"]`);
    if (el) {
      el.textContent = fmtCd(cd.remainingSec);
      el.style.color = sevColor(severity(cd.remainingSec, cd.totalSec));
    }
  });

  // A tournament went live or ended: re-filter, re-sort, fix badges.
  if (statusChanged) renderRows();

  // Update detail countdown
  if (state.selectedTag) {
    const t = state.tournaments.find(x => x.tag === state.selectedTag);
    if (t) updateDetailCountdown(t);
  }

  // Update fetch age text
  if (state.fetchedAt) $.fetchAge.textContent = fmtAgo(state.fetchedAt);
}

// Simple CSS.escape polyfill fallback
function cssEscape(s) {
  return (window.CSS && CSS.escape) ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&');
}

// ==========================================================
// Rendering — detail
// ==========================================================
function renderDetail() {
  if (!state.selectedTag) {
    $.detailName.textContent = 'Select a tournament';
    $.detailTag.textContent = '#—';
    $.detailLock.classList.add('hidden');
    $.detailCountdownValue.textContent = '00:00';
    $.detailCountdownBar.style.width = '0%';
    $.detailPlayers.textContent = '—';
    $.detailPlayersSub.textContent = '—';
    $.detailLevel.textContent = '—';
    $.detailMode.textContent = '—';
    $.detailAccess.textContent = '—';
    $.detailAccessSub.textContent = '—';
    $.detailTimingLine.textContent = '—';
    $.detailTimingSub.textContent = '—';
    $.detailJoin.removeAttribute('href');
    $.detailFav.classList.remove('favorited');
    $.detailFavBtn.classList.remove('favorited');
    $.detailFavBtn.textContent = '☆ Favorite';
    return;
  }
  const t = state.tournaments.find(x => x.tag === state.selectedTag);
  if (!t) return;
  const cleanTag = String(t.tag).replace('#', '');
  const fav = state.favorites.has(t.tag);
  const isPwd = t.type === 'passwordProtected';

  $.detailTag.textContent = `#${cleanTag}`;
  $.detailName.textContent = t.name || '—';
  $.detailLock.classList.toggle('hidden', !isPwd);

  $.detailFav.classList.toggle('favorited', fav);
  $.detailFavBtn.classList.toggle('favorited', fav);
  $.detailFavBtn.textContent = fav ? '★ Favorited' : '☆ Favorite';

  const pct = t.maxPlayers ? Math.round((t.players / t.maxPlayers) * 100) : 0;
  $.detailPlayers.textContent = `${t.players || 0}/${t.maxPlayers || 0}`;
  $.detailPlayersSub.textContent = `${pct}% full`;
  $.detailLevel.textContent = `${t.levelCap ?? '—'}`;
  $.detailMode.textContent = t.gameModeName || '—';
  $.detailAccess.textContent = isPwd ? '🔒 Private' : '🌐 Open';
  $.detailAccessSub.textContent = isPwd ? 'Password in-game' : 'Anyone can join';

  // Timing
  const created = parseCrTime(t.createdTime);
  const started = parseCrTime(t.startedTime);
  const duration = t.duration || 0;
  const durMin = Math.round(duration / 60);
  if (t.status === 'inPreparation' && created) {
    const startsAt = new Date(created.getTime() + (t.preparationDuration || 0) * 1000);
    $.detailTimingLine.textContent = `Starts ${fmtAbsTime(startsAt)}`;
    $.detailTimingSub.textContent = `Duration: ${durMin}m once started`;
  } else if (t.status === 'inProgress' && (started || created)) {
    const ref = started || new Date((created.getTime() + (t.preparationDuration || 0) * 1000));
    const endsAt = new Date(ref.getTime() + duration * 1000);
    $.detailTimingLine.textContent = `Ends ${fmtAbsTime(endsAt)}`;
    $.detailTimingSub.textContent = `Started ${fmtAbsTime(ref)} · ${durMin}m total`;
  } else {
    $.detailTimingLine.textContent = `Duration: ${durMin}m`;
    $.detailTimingSub.textContent = '—';
  }

  // Join link
  $.detailJoin.href = `https://link.clashroyale.com/en?clashroyale://joinTournament?id=${encodeURIComponent(cleanTag)}`;

  updateDetailCountdown(t);
}

function updateDetailCountdown(t) {
  const cd = computeCountdown(t);
  const sev = severity(cd.remainingSec, cd.totalSec);
  $.detailCountdownLabel.textContent = cd.countdownType === 'starts' ? 'Starts in' : cd.countdownType === 'ended' ? 'Ended' : 'Time remaining';
  $.detailCountdownValue.textContent = fmtCd(cd.remainingSec);
  const pct = cd.totalSec > 0 ? Math.min(100, (cd.remainingSec / cd.totalSec) * 100) : 0;
  $.detailCountdownBar.style.width = `${pct}%`;
  $.detailCountdownCard.classList.remove('sev-warn', 'sev-crit');
  if (sev === 'warn') $.detailCountdownCard.classList.add('sev-warn');
  else if (sev === 'crit') $.detailCountdownCard.classList.add('sev-crit');
}

// ==========================================================
// Helpers
// ==========================================================
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}
function escapeAttr(text) {
  return escapeHtml(text).replace(/"/g, '&quot;');
}

function showToast(msg) {
  $.toast.textContent = msg;
  $.toast.classList.remove('hidden');
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => $.toast.classList.add('hidden'), 2000);
}

function toggleFavorite(tag) {
  if (state.favorites.has(tag)) {
    state.favorites.delete(tag);
  } else {
    state.favorites.add(tag);
    // Ask once for permission so we can notify when a favorite goes live.
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  }
  persistFavorites();
  renderRows();
}

function notifyFavoriteLive(t) {
  showToast(`★ ${t.name || t.tag} is live!`);
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    new Notification('Tournament live', {
      body: `${t.name || t.tag} just started — join now!`,
      tag: `cr-live-${t.tag}`,
      icon: '/static/icons/icon-192x192.png',
    });
  } catch {}
}

// ==========================================================
// Responsive
// ==========================================================
function applyResponsive() {
  const w = window.innerWidth;
  state.sidebarOverlay = w < SIDEBAR_BREAK;
  state.detailOverlay = w < DETAIL_BREAK;

  if ($.searchInput) {
    $.searchInput.placeholder = w < 560 ? 'Search name or tag' : 'Search name, tag, or mode…';
  }

  if (!state.sidebarOverlay) {
    $.sidebar.classList.remove('open');
    $.sidebarBackdrop.classList.add('hidden');
    state.sidebarOpen = false;
  }
  if (!state.detailOverlay) {
    $.detail.classList.remove('open');
    $.detail.classList.remove('hidden');
    $.detailBackdrop.classList.add('hidden');
    state.detailOpen = false;
  } else {
    if (!state.detailOpen) {
      $.detail.classList.add('hidden');
    }
  }
}

function openSidebar() {
  state.sidebarOpen = true;
  $.sidebar.classList.add('open');
  $.sidebarBackdrop.classList.remove('hidden');
}
function closeSidebar() {
  state.sidebarOpen = false;
  $.sidebar.classList.remove('open');
  $.sidebarBackdrop.classList.add('hidden');
}
function openDetail() {
  state.detailOpen = true;
  $.detail.classList.remove('hidden');
  $.detail.classList.add('open');
  $.detailBackdrop.classList.remove('hidden');
}
function closeDetail() {
  state.detailOpen = false;
  $.detail.classList.remove('open');
  if (state.detailOverlay) $.detail.classList.add('hidden');
  $.detailBackdrop.classList.add('hidden');
}

// ==========================================================
// API calls
// ==========================================================
async function loadGameModes() {
  try {
    const r = await fetch('/api/game-modes');
    state.gameModes = await r.json();
  } catch (e) { console.error('game modes', e); }
}

async function loadConfig() {
  try {
    const r = await fetch('/api/config');
    const data = await r.json();
    state.hasApiKey = !!data.has_api_key;
    state.apiKeyFromEnv = !!data.api_key_from_env;
    state.shutdownEnabled = data.shutdown_enabled !== false;

    $.shutdownBtn.classList.toggle('hidden', !state.shutdownEnabled);

    if (state.apiKeyFromEnv) {
      $.keyCurrentRow.classList.remove('hidden');
      $.keyInputRow.classList.add('hidden');
      $.maskedKey.textContent = '(env variable)';
      $.changeKeyBtn.classList.add('hidden');
    } else if (state.hasApiKey) {
      $.keyCurrentRow.classList.remove('hidden');
      $.keyInputRow.classList.add('hidden');
      $.maskedKey.textContent = data.masked_key || '—';
      $.changeKeyBtn.classList.remove('hidden');
    } else {
      $.keyCurrentRow.classList.add('hidden');
      $.keyInputRow.classList.remove('hidden');
    }

    if (data.filters) applySavedFilters(data.filters);
  } catch (e) { console.error('config', e); }
}

function finiteNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function optionalFiniteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function syncFilterControls() {
  $.minPlayers.value = state.filters.minPlayers || 0;
  $.minRemaining.value = state.filters.minMinsLeft || '';
  $.maxRemaining.value = state.filters.maxMinsLeft ?? '';
  $.accessRadio.querySelectorAll('input[name="access"]').forEach(r => { r.checked = r.value === state.filters.access; });
  renderModeChecks();
  renderLevelCaps();
}

function applySavedFilters(f) {
  state.filters.modes = new Set(Array.isArray(f.game_modes) ? f.game_modes.map(String) : []);
  state.filters.levelCaps = new Set(
    (Array.isArray(f.level_caps) ? f.level_caps : [])
      .map(Number)
      .filter(Number.isFinite)
  );
  state.filters.minPlayers = finiteNumber(f.min_players, 0);
  state.filters.minMinsLeft = finiteNumber(f.min_remaining_minutes, 0);

  state.filters.maxMinsLeft = optionalFiniteNumber(f.max_remaining_minutes);

  if (f.tournament_type === 'open') state.filters.access = 'open';
  else if (f.tournament_type === 'password') state.filters.access = 'password';
  else state.filters.access = 'any';

  syncFilterControls();
}

async function saveDefaults() {
  const filters = {
    tournament_type: state.filters.access === 'any' ? 'all' : state.filters.access,
    status: 'all',
    game_modes: [...state.filters.modes],
    level_caps: [...state.filters.levelCaps].map(String),
    min_players: state.filters.minPlayers,
    min_remaining_minutes: state.filters.minMinsLeft,
    max_remaining_minutes: state.filters.maxMinsLeft,
  };
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filters }),
    });
    if (r.ok) showToast('Defaults saved');
  } catch { showToast('Failed to save defaults'); }
}

async function saveApiKey() {
  const key = $.apiKeyInput.value.trim();
  if (!key) { showToast('Enter an API key'); return; }
  try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: key }),
    });
    if (r.ok) {
      $.apiKeyInput.value = '';
      await loadConfig();
      showToast('API key saved');
      searchTournaments({ force: false });
    } else {
      showToast('Failed to save key');
    }
  } catch { showToast('Failed to save key'); }
}

function showProgress(text) {
  $.progress.classList.remove('hidden');
  $.progress.classList.add('indeterminate');
  $.progressText.textContent = text || 'Searching…';
  $.progressFill.style.width = '0%';
  $.progressMetrics.textContent = '';
}
function hideProgress() {
  $.progress.classList.add('hidden');
  $.progress.classList.remove('indeterminate');
}
function updateProgress(p) {
  if (!p) return;
  const phase = p.phase || 'working';
  let label = 'Working…';
  if (phase === 'cache') label = 'Cached crawl';
  else if (phase === 'crawl') label = 'Crawling tournaments';
  else if (phase === 'verify') label = 'Verifying coverage';
  else if (phase === 'details') label = 'Fetching start times';
  else if (phase === 'serialize') label = 'Preparing results';

  $.progressText.textContent = label;

  const completed = Number(p.completed ?? p.done ?? 0);
  const pending = Number(p.pending ?? 0);
  const total = Number(p.total ?? (completed + pending) ?? 0);
  let pct = null;
  if (total > 0 && completed >= 0) pct = Math.max(0, Math.min(100, Math.floor((completed / total) * 100)));

  if (pct === null) {
    $.progress.classList.add('indeterminate');
    $.progressFill.style.width = '30%';
    $.progressMetrics.textContent = '';
  } else {
    $.progress.classList.remove('indeterminate');
    $.progressFill.style.width = `${pct}%`;
    $.progressMetrics.textContent = phase === 'details' && total
      ? `${completed}/${total}`
      : `${pct}%`;
  }
}

function applySearchResponse(data) {
  if (data.error) {
    showToast('Error: ' + data.error);
    return false;
  }
  state.tournaments = data.tournaments || [];
  state.fetchedAt = data.fetchedAt || new Date().toISOString();
  state.lastStats = data.stats || null;
  renderRows();
  if (state.lastStats) updateDebugStats(state.lastStats);
  $.fetchAge.textContent = fmtAgo(state.fetchedAt);
  showToast(`Loaded ${state.tournaments.length} tournaments`);
  return true;
}

async function searchTournaments({ force = false } = {}) {
  if (!state.hasApiKey) {
    showToast('Set your API key in Settings first');
    openSettings();
    return;
  }
  if (state.isSearching) return;
  state.isSearching = true;
  $.refreshBtn.disabled = true;
  showProgress('Starting…');

  if ('EventSource' in window) {
    try {
      if (state.activeStream) { state.activeStream.close(); state.activeStream = null; }
      const url = force ? '/api/tournaments/search/stream?force=1' : '/api/tournaments/search/stream';
      const es = new EventSource(url);
      state.activeStream = es;
      let gotAny = false;

      es.addEventListener('progress', ev => {
        gotAny = true;
        try { updateProgress(JSON.parse(ev.data)); } catch {}
      });
      es.addEventListener('done', ev => {
        gotAny = true;
        try { applySearchResponse(JSON.parse(ev.data)); }
        catch { showToast('Invalid server response'); }
        finally { es.close(); state.activeStream = null; state.isSearching = false; hideProgress(); $.refreshBtn.disabled = false; }
      });
      es.addEventListener('fail', ev => {
        gotAny = true;
        try { const d = JSON.parse(ev.data); showToast('Error: ' + (d.error || 'search failed')); }
        catch { showToast('Search failed'); }
        finally { es.close(); state.activeStream = null; state.isSearching = false; hideProgress(); $.refreshBtn.disabled = false; }
      });
      es.onerror = () => {
        if (!gotAny) showToast('Connection failed');
        es.close(); state.activeStream = null; state.isSearching = false; hideProgress(); $.refreshBtn.disabled = false;
      };
      return;
    } catch (e) { console.error('SSE fail', e); /* fall through */ }
  }

  // Fallback: plain fetch
  try {
    const url = force ? '/api/tournaments/search?force=1' : '/api/tournaments/search';
    const r = await fetch(url);
    const data = await r.json();
    applySearchResponse(data);
  } catch {
    showToast('Search failed');
  } finally {
    state.isSearching = false;
    hideProgress();
    $.refreshBtn.disabled = false;
  }
}

// ==========================================================
// Debug / settings
// ==========================================================
function updateDebugStats(s) {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('stat-queries', s.queries ?? 0);
  set('stat-confidence', s.confidence || 'unknown');
  set('stat-retried', s.retriedQueries ?? 0);
  set('stat-drilldowns', s.drillDowns ?? 0);
  set('stat-ratelimits', s.rateLimits ?? 0);
  set('stat-errors', s.apiErrors ?? 0);
  set('stat-verification-passes', s.verificationPasses ?? 0);
  set('stat-failed-queries', s.failedQueries ?? 0);
  set('stat-saturated-queries', s.saturatedQueries ?? 0);

  if (s.tournamentsByMode) {
    $.modeBreakdown.classList.remove('hidden');
    $.modeList.innerHTML = '';
    Object.entries(s.tournamentsByMode)
      .sort((a, b) => b[1] - a[1])
      .forEach(([id, count]) => {
        const name = state.gameModes[id] || `#${id}`;
        const div = document.createElement('div');
        div.className = 'mode-item';
        div.innerHTML = `<span class="mode-name">${escapeHtml(name)}</span><span class="mode-count">${count}</span>`;
        $.modeList.appendChild(div);
      });
  } else {
    $.modeBreakdown.classList.add('hidden');
  }
}

async function loadLogs() {
  try {
    $.logOutput.textContent = 'Loading…';
    const r = await fetch('/api/logs?lines=200');
    const data = await r.json();
    $.logOutput.textContent = (data.logs && data.logs.length) ? data.logs.join('') : 'No logs yet.';
    $.logOutput.scrollTop = $.logOutput.scrollHeight;
  } catch (e) {
    $.logOutput.textContent = 'Error loading logs: ' + e.message;
  }
}

async function searchTag() {
  const tag = $.tagSearchInput.value.trim();
  if (!tag) {
    $.tagSearchResult.className = 'db-log-result warning';
    $.tagSearchResult.textContent = 'Enter a tournament tag';
    $.tagSearchResult.classList.remove('hidden');
    return;
  }
  try {
    const r = await fetch('/api/logs?search=' + encodeURIComponent(tag));
    const data = await r.json();
    if (data.total_matches > 0) {
      $.tagSearchResult.className = 'db-log-result success';
      $.tagSearchResult.innerHTML = `<strong>Found ${data.total_matches} entries for ${escapeHtml(tag)}</strong><pre>${escapeHtml((data.logs || []).join(''))}</pre>`;
    } else {
      $.tagSearchResult.className = 'db-log-result warning';
      $.tagSearchResult.innerHTML = `<strong>No entries for ${escapeHtml(tag)}</strong><br>The tournament was not in the search results.`;
    }
    $.tagSearchResult.classList.remove('hidden');
  } catch (e) {
    $.tagSearchResult.className = 'db-log-result error';
    $.tagSearchResult.textContent = 'Error: ' + e.message;
    $.tagSearchResult.classList.remove('hidden');
  }
}

async function shutdownServer() {
  if (!confirm('Shutdown the server?')) return;
  try {
    const r = await fetch('/api/shutdown', { method: 'POST' });
    if (!r.ok) {
      let msg = 'Shutdown disabled.';
      try { const d = await r.json(); if (d.error) msg = d.error; } catch {}
      showToast(msg);
      return;
    }
    clearInterval(state.heartbeatInterval);
    showToast('Server shutting down…');
    setTimeout(() => {
      document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:Inter,system-ui;color:#a3adba;background:#0a0d12;"><div style="text-align:center;"><h2 style="color:#f4d03f;letter-spacing:1.5px;text-transform:uppercase;">Server stopped</h2><p>You can close this tab.</p></div></div>';
    }, 500);
  } catch { showToast('Shutdown failed'); }
}

function openSettings() {
  if (state.sidebarOverlay && state.sidebarOpen) closeSidebar();
  if (state.detailOverlay && state.detailOpen) closeDetail();
  $.settings.classList.remove('hidden');
}
function closeSettings() { $.settings.classList.add('hidden'); }

// ==========================================================
// Heartbeat
// ==========================================================
function startHeartbeat() {
  state.heartbeatInterval = setInterval(() => {
    if (state.isSearching || state.activeStream) return;
    fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
  }, 30000);
  fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
}

// ==========================================================
// Wire up
// ==========================================================
function debounce(fn, wait) {
  let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
}

function wire() {
  // Sidebar overlay open/close
  $.sidebarOpen.addEventListener('click', openSidebar);
  $.sidebarClose.addEventListener('click', closeSidebar);
  $.sidebarBackdrop.addEventListener('click', closeSidebar);

  // Detail close
  $.detailClose.addEventListener('click', closeDetail);
  $.detailBackdrop.addEventListener('click', closeDetail);

  // Sidebar: levels
  $.levelCaps.querySelectorAll('.db-lvl').forEach(btn => {
    btn.addEventListener('click', () => {
      const n = Number(btn.dataset.value);
      if (state.filters.levelCaps.has(n)) state.filters.levelCaps.delete(n);
      else state.filters.levelCaps.add(n);
      renderLevelCaps();
      renderRows();
    });
  });

  // Sidebar: numeric inputs
  const rerenderDebounced = debounce(renderRows, 200);
  $.minPlayers.addEventListener('input', () => {
    state.filters.minPlayers = Number($.minPlayers.value) || 0;
    rerenderDebounced();
  });
  $.minRemaining.addEventListener('input', () => {
    state.filters.minMinsLeft = Number($.minRemaining.value) || 0;
    rerenderDebounced();
  });
  $.maxRemaining.addEventListener('input', () => {
    state.filters.maxMinsLeft = Number($.maxRemaining.value) || null;
    rerenderDebounced();
  });

  // Access radios
  $.accessRadio.querySelectorAll('input[name="access"]').forEach(r => {
    r.addEventListener('change', () => {
      state.filters.access = r.value;
      renderRows();
    });
  });

  // Clear / save defaults
  $.clearFiltersBtn.addEventListener('click', () => {
    state.filters = { modes: new Set(), levelCaps: new Set(), minPlayers: 0, minMinsLeft: 0, maxMinsLeft: null, access: 'any' };
    state.savedView = 'all';
    state.quick = null;
    $.minPlayers.value = 0;
    $.minRemaining.value = '';
    $.maxRemaining.value = '';
    $.accessRadio.querySelector('input[value="any"]').checked = true;
    renderModeChecks();
    renderLevelCaps();
    renderRows();
  });
  $.saveDefaultsBtn.addEventListener('click', saveDefaults);

  // Toolbar
  $.searchInput.addEventListener('input', debounce(() => { state.search = $.searchInput.value; renderRows(); }, 100));
  $.quickLive.addEventListener('click', () => { state.quick = state.quick === 'live' ? null : 'live'; renderRows(); });
  $.quickPrep.addEventListener('click', () => { state.quick = state.quick === 'prep' ? null : 'prep'; renderRows(); });
  // Normal click reuses the server cache (fast); Shift-click forces a full re-crawl.
  $.refreshBtn.addEventListener('click', e => searchTournaments({ force: e.shiftKey }));
  if ($.autoRefreshBtn) {
    $.autoRefreshBtn.classList.toggle('active', state.autoRefresh);
    $.autoRefreshBtn.addEventListener('click', () => {
      state.autoRefresh = !state.autoRefresh;
      try { localStorage.setItem('cr.autoRefresh', state.autoRefresh ? '1' : '0'); } catch {}
      $.autoRefreshBtn.classList.toggle('active', state.autoRefresh);
      showToast(state.autoRefresh ? 'Auto-refresh on (every 3 min)' : 'Auto-refresh off');
    });
  }

  // Sort
  $.thead.querySelectorAll('.db-th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (state.sort.by === key) {
        state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        state.sort = { by: key, dir: 'asc' };
      }
      renderRows();
    });
  });

  // Detail actions
  $.detailFav.addEventListener('click', () => { if (state.selectedTag) toggleFavorite(state.selectedTag); });
  $.detailFavBtn.addEventListener('click', () => { if (state.selectedTag) toggleFavorite(state.selectedTag); });
  const copyCurrentTag = () => {
    if (!state.selectedTag) return;
    const cleanTag = String(state.selectedTag).replace('#', '');
    copyToClipboard(`#${cleanTag}`);
  };
  $.detailCopy.addEventListener('click', copyCurrentTag);
  $.detailCopyBtn.addEventListener('click', copyCurrentTag);

  // Settings drawer
  $.settingsBtn.addEventListener('click', openSettings);
  $.settingsClose.addEventListener('click', closeSettings);
  $.saveApiKey.addEventListener('click', saveApiKey);
  $.apiKeyInput.addEventListener('keypress', e => { if (e.key === 'Enter') saveApiKey(); });
  $.toggleVisibility.addEventListener('click', () => {
    const next = $.apiKeyInput.type === 'password' ? 'text' : 'password';
    $.apiKeyInput.type = next;
    $.toggleVisibility.textContent = next === 'text' ? '🙈' : '👁';
  });
  $.changeKeyBtn.addEventListener('click', () => {
    $.keyCurrentRow.classList.add('hidden');
    $.keyInputRow.classList.remove('hidden');
    $.apiKeyInput.focus();
  });
  $.tagSearchBtn.addEventListener('click', searchTag);
  $.tagSearchInput.addEventListener('keypress', e => { if (e.key === 'Enter') searchTag(); });
  $.refreshLogsBtn.addEventListener('click', loadLogs);
  $.shutdownBtn.addEventListener('click', shutdownServer);

  // Global shortcuts
  window.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      $.searchInput.focus();
      $.searchInput.select();
    } else if (e.key === 'Escape') {
      if (!$.settings.classList.contains('hidden')) closeSettings();
      else if (state.detailOverlay && state.detailOpen) closeDetail();
      else if (state.sidebarOverlay && state.sidebarOpen) closeSidebar();
    }
  });

  // Resize handler
  window.addEventListener('resize', debounce(applyResponsive, 100));
}

function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => showToast('Copied: ' + text));
    return;
  }
  const input = document.createElement('input');
  input.value = text;
  document.body.appendChild(input);
  input.select();
  try { document.execCommand('copy'); showToast('Copied: ' + text); } catch {}
  document.body.removeChild(input);
}

// ==========================================================
// Boot
// ==========================================================
document.addEventListener('DOMContentLoaded', async () => {
  grabRefs();
  wire();
  applyResponsive();
  renderSavedViews();
  renderLevelCaps();

  await loadGameModes();
  renderModeChecks();

  await loadConfig();
  renderRows(); // initial empty/"set API key" state

  startHeartbeat();

  // Tick every second for countdowns + fetch-age
  setInterval(tickCountdowns, 1000);

  // Auto-refresh: re-fetch (cache-friendly) when data is stale and the tab is visible.
  setInterval(() => {
    if (!state.autoRefresh || state.isSearching || !state.hasApiKey) return;
    if (document.visibilityState !== 'visible') return;
    const ageSec = state.fetchedAt ? (Date.now() - new Date(state.fetchedAt).getTime()) / 1000 : Infinity;
    if (ageSec >= 170) searchTournaments({ force: false });
  }, 15000);

  // Kick off an initial search if we have a key
  if (state.hasApiKey) {
    searchTournaments({ force: false });
  }
});
