// Clash Royale Tournament Finder - Frontend Logic

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/service-worker.js')
        .then((registration) => {
            console.log('Service Worker registered:', registration.scope);
        })
        .catch((error) => {
            console.log('Service Worker registration failed:', error);
        });
}

document.addEventListener('DOMContentLoaded', () => {
    // Elements
    const apiKeySection = document.getElementById('api-key-section');
    const apiKeyContent = document.getElementById('api-key-content');
    const apiKeyInput = document.getElementById('api-key-input');
    const saveApiKeyBtn = document.getElementById('save-api-key');
    const toggleKeySection = document.getElementById('toggle-key-section');
    const toggleVisibility = document.getElementById('toggle-visibility');
    const currentKeyDisplay = document.getElementById('current-key-display');
    const maskedKey = document.getElementById('masked-key');
    const changeKeyBtn = document.getElementById('change-key-btn');
    const keyInputSection = document.getElementById('key-input-section');
    const filtersSection = document.getElementById('filters-section');
    const searchBtn = document.getElementById('search-btn');
    const saveDefaultsBtn = document.getElementById('save-defaults-btn');
    const clearFiltersBtn = document.getElementById('clear-filters-btn');
    const filterBadge = document.getElementById('filter-badge');
    const statusMessage = document.getElementById('status-message');
    const resultsSection = document.getElementById('results-section');
    const resultsCount = document.getElementById('results-count');
    const resultsBody = document.getElementById('results-body');
    const resultsCards = document.getElementById('results-cards');
    const toast = document.getElementById('toast');
    const shutdownBtn = document.getElementById('shutdown-btn');

    // Debug elements
    const debugContent = document.getElementById('debug-content');
    const toggleDebug = document.getElementById('toggle-debug');
    const debugStats = document.getElementById('debug-stats');
    const tagSearchInput = document.getElementById('tag-search-input');
    const tagSearchBtn = document.getElementById('tag-search-btn');
    const tagSearchResult = document.getElementById('tag-search-result');
    const refreshLogsBtn = document.getElementById('refresh-logs-btn');
    const logOutput = document.getElementById('log-output');

    // Game modes cache for debug display
    let gameModeNames = {};

    // Filter elements
    const tournamentType = document.getElementById('tournament-type');
    const statusFilter = document.getElementById('status');
    const gameModePills = document.getElementById('game-mode-pills');
    const levelCapToggles = document.getElementById('level-cap-toggles');
    const minPlayers = document.getElementById('min-players');
    const maxPlayers = document.getElementById('max-players');
    const minRemaining = document.getElementById('min-remaining');
    const maxRemaining = document.getElementById('max-remaining');

    let hasApiKey = false;
    let keyVisible = false;
    let heartbeatInterval = null;

    // Initialize
    init();

    async function init() {
        await loadGameModes();
        await loadConfig();
        setupEventListeners();
        startHeartbeat();
        updateFilterBadge();
    }

    function setupEventListeners() {
        // Level cap toggle buttons
        levelCapToggles.querySelectorAll('.toggle-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                btn.classList.toggle('active');
                updateFilterBadge();
            });
        });

        // Clear filters
        clearFiltersBtn.addEventListener('click', clearAllFilters);

        // Update badge on filter changes
        tournamentType.addEventListener('change', updateFilterBadge);
        statusFilter.addEventListener('change', updateFilterBadge);
        minPlayers.addEventListener('input', updateFilterBadge);
        maxPlayers.addEventListener('input', updateFilterBadge);
        minRemaining.addEventListener('input', updateFilterBadge);
        maxRemaining.addEventListener('input', updateFilterBadge);
    }

    function startHeartbeat() {
        // Send heartbeat every 30 seconds to keep server alive
        heartbeatInterval = setInterval(async () => {
            try {
                await fetch('/api/heartbeat', { method: 'POST' });
            } catch (err) {
                // Server might be down
                console.log('Heartbeat failed - server may be offline');
            }
        }, 30000);

        // Send initial heartbeat
        fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
    }

    async function loadConfig() {
        try {
            const resp = await fetch('/api/config');
            const data = await resp.json();

            hasApiKey = data.has_api_key;

            if (data.api_key_from_env) {
                // API key is set via environment variable - hide entire section
                currentKeyDisplay.classList.remove('hidden');
                keyInputSection.classList.add('hidden');
                maskedKey.textContent = '(Set via Environment Variable)';
                changeKeyBtn.classList.add('hidden');
                // Collapse section by default
                apiKeyContent.classList.add('collapsed');
                toggleKeySection.classList.add('rotated');
            } else if (hasApiKey && data.masked_key) {
                // Show current key info, hide input
                currentKeyDisplay.classList.remove('hidden');
                keyInputSection.classList.add('hidden');
                maskedKey.textContent = data.masked_key;
                changeKeyBtn.classList.remove('hidden');
                // Collapse section by default when key exists
                apiKeyContent.classList.add('collapsed');
                toggleKeySection.classList.add('rotated');
            } else {
                // Show input, hide current key
                currentKeyDisplay.classList.add('hidden');
                keyInputSection.classList.remove('hidden');
            }

            // Apply saved filters
            if (data.filters) {
                applyFilters(data.filters);
            }
        } catch (err) {
            console.error('Failed to load config:', err);
        }
    }

    async function loadGameModes() {
        try {
            const resp = await fetch('/api/game-modes');
            const modes = await resp.json();

            gameModeNames = modes; // Cache for debug display
            renderGameModePills(modes);
        } catch (err) {
            console.error('Failed to load game modes:', err);
        }
    }

    function renderGameModePills(modes) {
        gameModePills.innerHTML = '';
        for (const [id, name] of Object.entries(modes)) {
            const pill = document.createElement('button');
            pill.type = 'button';
            pill.className = 'pill';
            pill.dataset.value = id;
            pill.textContent = name;
            pill.addEventListener('click', () => {
                pill.classList.toggle('active');
                updateFilterBadge();
            });
            gameModePills.appendChild(pill);
        }
    }

    function applyFilters(filters) {
        if (filters.tournament_type) {
            tournamentType.value = filters.tournament_type;
        }
        if (filters.status) {
            statusFilter.value = filters.status;
        }
        if (filters.min_players !== undefined) {
            minPlayers.value = filters.min_players || '';
        }
        if (filters.max_players) {
            maxPlayers.value = filters.max_players;
        }
        if (filters.min_remaining_minutes !== undefined) {
            minRemaining.value = filters.min_remaining_minutes || '';
        }
        if (filters.max_remaining_minutes) {
            maxRemaining.value = filters.max_remaining_minutes;
        }
        // Game mode pills
        if (filters.game_modes && filters.game_modes.length) {
            gameModePills.querySelectorAll('.pill').forEach(pill => {
                if (filters.game_modes.includes(pill.dataset.value)) {
                    pill.classList.add('active');
                }
            });
        }
        // Level cap toggles
        if (filters.level_caps && filters.level_caps.length) {
            levelCapToggles.querySelectorAll('.toggle-btn').forEach(btn => {
                if (filters.level_caps.includes(btn.dataset.value)) {
                    btn.classList.add('active');
                }
            });
        }
        updateFilterBadge();
    }

    function getFilters() {
        return {
            tournament_type: tournamentType.value,
            status: statusFilter.value,
            game_modes: Array.from(gameModePills.querySelectorAll('.pill.active'))
                .map(p => p.dataset.value),
            level_caps: Array.from(levelCapToggles.querySelectorAll('.toggle-btn.active'))
                .map(b => b.dataset.value),
            min_players: parseInt(minPlayers.value) || 0,
            max_players: parseInt(maxPlayers.value) || null,
            min_remaining_minutes: parseInt(minRemaining.value) || 0,
            max_remaining_minutes: parseInt(maxRemaining.value) || null
        };
    }

    function countActiveFilters() {
        let count = 0;
        // Tournament type (if not default)
        if (tournamentType.value !== 'open') count++;
        // Status (if not default)
        if (statusFilter.value !== 'all') count++;
        // Game modes
        count += gameModePills.querySelectorAll('.pill.active').length;
        // Level caps
        count += levelCapToggles.querySelectorAll('.toggle-btn.active').length;
        // Player filters
        if (parseInt(minPlayers.value) > 0) count++;
        if (maxPlayers.value) count++;
        // Time filters
        if (parseInt(minRemaining.value) > 0) count++;
        if (maxRemaining.value) count++;
        return count;
    }

    function updateFilterBadge() {
        const count = countActiveFilters();
        if (count > 0) {
            filterBadge.textContent = count;
            filterBadge.classList.remove('hidden');
        } else {
            filterBadge.classList.add('hidden');
        }
    }

    function clearAllFilters() {
        // Reset dropdowns
        tournamentType.value = 'open';
        statusFilter.value = 'all';
        // Reset pills
        gameModePills.querySelectorAll('.pill.active').forEach(p => p.classList.remove('active'));
        // Reset toggles
        levelCapToggles.querySelectorAll('.toggle-btn.active').forEach(b => b.classList.remove('active'));
        // Reset number inputs
        minPlayers.value = '0';
        maxPlayers.value = '';
        minRemaining.value = '0';
        maxRemaining.value = '';
        updateFilterBadge();
    }

    function showStatus(message, type) {
        statusMessage.textContent = message;
        statusMessage.className = 'status-message ' + type;
        statusMessage.classList.remove('hidden');
    }

    function hideStatus() {
        statusMessage.classList.add('hidden');
    }

    function showToast(message) {
        toast.textContent = message;
        toast.classList.remove('hidden');
        toast.classList.add('show');
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.classList.add('hidden'), 400);
        }, 2000);
    }

    function setLoading(loading) {
        const btnText = searchBtn.querySelector('.btn-text');
        const btnLoading = searchBtn.querySelector('.btn-loading');

        if (loading) {
            btnText.classList.add('hidden');
            btnLoading.classList.remove('hidden');
            searchBtn.disabled = true;
        } else {
            btnText.classList.remove('hidden');
            btnLoading.classList.add('hidden');
            searchBtn.disabled = false;
        }
    }

    async function searchTournaments() {
        if (!hasApiKey) {
            showStatus('Please enter your API key first', 'error');
            return;
        }

        setLoading(true);
        showStatus('Searching tournaments... This may take a few seconds.', 'loading');

        try {
            const filters = getFilters();
            const params = new URLSearchParams();

            params.set('tournament_type', filters.tournament_type);
            params.set('status', filters.status);
            params.set('min_players', filters.min_players);
            if (filters.max_players) params.set('max_players', filters.max_players);
            params.set('min_remaining_minutes', filters.min_remaining_minutes);
            if (filters.max_remaining_minutes) params.set('max_remaining_minutes', filters.max_remaining_minutes);

            filters.game_modes.forEach(m => params.append('game_modes', m));
            filters.level_caps.forEach(l => params.append('level_caps', l));

            const resp = await fetch('/api/tournaments?' + params.toString());
            const data = await resp.json();

            if (data.error) {
                showStatus('Error: ' + data.error, 'error');
                resultsSection.classList.add('hidden');
                return;
            }

            displayResults(data);
            showStatus(`Found ${data.total} tournaments (${data.unfilteredTotal} total before filters)`, 'success');

            // Update debug stats
            if (data.stats) {
                updateDebugStats(data.stats);
            }

        } catch (err) {
            console.error('Search failed:', err);
            showStatus('Search failed. Check your connection and API key.', 'error');
        } finally {
            setLoading(false);
        }
    }

    function displayResults(data) {
        const tournaments = data.tournaments;

        if (tournaments.length === 0) {
            resultsBody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-secondary); padding: 32px;">No tournaments found matching your filters</td></tr>';
            resultsCards.innerHTML = '<div class="empty-state"><p>No tournaments found matching your filters</p></div>';
            resultsSection.classList.remove('hidden');
            resultsCount.textContent = '';
            return;
        }

        resultsCount.textContent = `(${tournaments.length})`;

        // Desktop table
        displayTableResults(tournaments);

        // Mobile cards
        displayCardResults(tournaments);

        resultsSection.classList.remove('hidden');
    }

    function displayTableResults(tournaments) {
        resultsBody.innerHTML = '';

        tournaments.forEach(t => {
            const tr = document.createElement('tr');

            // Format remaining time
            const timeLeft = formatTime(t.remainingMinutes);

            // Format elapsed time (running for)
            const elapsed = formatTime(t.elapsedMinutes);

            // Status badge
            const statusClass = t.status === 'inProgress' ? 'in-progress' : 'in-preparation';
            const statusText = t.status === 'inProgress' ? 'In Progress' : 'Preparation';

            // Join URL
            const joinUrl = getTournamentJoinUrl(t.tag);
            const isPasswordProtected = t.type === 'passwordProtected';
            const lockIcon = isPasswordProtected ? '<span class="lock-icon" title="Password required">&#128274;</span>' : '';

            tr.innerHTML = `
                <td>${escapeHtml(t.name)}</td>
                <td>${escapeHtml(t.gameMode)}</td>
                <td>${t.players}/${t.maxPlayers}</td>
                <td>${t.levelCap}</td>
                <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                <td>${elapsed}</td>
                <td>${timeLeft}</td>
                <td class="tag-cell" data-tag="${escapeHtml(t.tag)}">${escapeHtml(t.tag)}</td>
                <td><a href="${joinUrl}" target="_blank" rel="noopener" class="join-btn">${lockIcon}Join</a></td>
            `;

            resultsBody.appendChild(tr);
        });
    }

    function displayCardResults(tournaments) {
        resultsCards.innerHTML = '';

        tournaments.forEach((t, index) => {
            const card = document.createElement('div');
            card.className = 'result-card';
            card.style.animationDelay = `${Math.min(index * 0.05, 0.5)}s`;

            const timeLeft = formatTime(t.remainingMinutes);
            const timePercent = calculateTimePercent(t);
            const timeClass = timePercent < 20 ? 'low' : timePercent < 50 ? 'medium' : '';

            const statusClass = t.status === 'inProgress' ? 'in-progress' : 'in-preparation';
            const statusText = t.status === 'inProgress' ? 'In Progress' : 'Preparation';

            // Join URL
            const joinUrl = getTournamentJoinUrl(t.tag);
            const isPasswordProtected = t.type === 'passwordProtected';
            const lockIcon = isPasswordProtected ? '<span class="lock-icon" title="Password required">&#128274;</span>' : '';

            card.innerHTML = `
                <div class="result-card-header">
                    <span class="result-card-name">${escapeHtml(t.name)}</span>
                    <span class="result-card-mode">${escapeHtml(t.gameMode)}</span>
                </div>
                <div class="result-card-stats">
                    <div class="result-card-stat">
                        <div class="result-card-stat-value">${t.players}/${t.maxPlayers}</div>
                        <div class="result-card-stat-label">Players</div>
                    </div>
                    <div class="result-card-stat">
                        <div class="result-card-stat-value">${t.levelCap}</div>
                        <div class="result-card-stat-label">Level Cap</div>
                    </div>
                    <div class="result-card-stat">
                        <div class="result-card-stat-value">${timeLeft}</div>
                        <div class="result-card-stat-label">Time Left</div>
                    </div>
                </div>
                <div class="time-progress">
                    <div class="time-progress-bar ${timeClass}" style="width: ${timePercent}%"></div>
                </div>
                <div class="result-card-footer">
                    <span class="status-badge ${statusClass}">${statusText}</span>
                    <div class="result-card-actions">
                        <button class="result-card-tag" data-tag="${escapeHtml(t.tag)}">${escapeHtml(t.tag)}</button>
                        <a href="${joinUrl}" target="_blank" rel="noopener" class="join-btn-mobile">${lockIcon}Join</a>
                    </div>
                </div>
            `;

            resultsCards.appendChild(card);
        });
    }

    function formatTime(minutes) {
        if (minutes === null || minutes === undefined) return '—';
        if (minutes < 60) {
            return `${minutes}m`;
        } else {
            const hours = Math.floor(minutes / 60);
            const mins = minutes % 60;
            return `${hours}h ${mins}m`;
        }
    }

    function calculateTimePercent(tournament) {
        // Estimate: assume max tournament duration is 60 minutes
        // If we have remaining time, calculate percentage
        if (tournament.remainingMinutes === null) return 100;
        const maxDuration = 60; // minutes
        const percent = (tournament.remainingMinutes / maxDuration) * 100;
        return Math.min(100, Math.max(0, percent));
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function getTournamentJoinUrl(tag) {
        // Remove # prefix if present and construct deep link URL
        const cleanTag = tag.replace('#', '');
        return `https://link.clashroyale.com/en?clashroyale://joinTournament?id=${cleanTag}`;
    }

    async function copyTag(tag) {
        try {
            await navigator.clipboard.writeText(tag);
            showToast('Copied: ' + tag);
        } catch (err) {
            // Fallback
            const input = document.createElement('input');
            input.value = tag;
            document.body.appendChild(input);
            input.select();
            document.execCommand('copy');
            document.body.removeChild(input);
            showToast('Copied: ' + tag);
        }
    }

    async function saveApiKey() {
        const key = apiKeyInput.value.trim();
        if (!key) {
            showStatus('Please enter an API key', 'error');
            return;
        }

        try {
            const resp = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: key })
            });

            if (resp.ok) {
                hasApiKey = true;
                apiKeyInput.value = '';
                hideStatus();
                showToast('API key saved!');
                // Reload to show masked key
                await loadConfig();
            }
        } catch (err) {
            showStatus('Failed to save API key', 'error');
        }
    }

    async function saveDefaults() {
        const filters = getFilters();

        try {
            const resp = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filters })
            });

            if (resp.ok) {
                showToast('Defaults saved!');
            }
        } catch (err) {
            showStatus('Failed to save defaults', 'error');
        }
    }

    function toggleApiKeySection() {
        apiKeyContent.classList.toggle('collapsed');
        toggleKeySection.classList.toggle('rotated');
    }

    function showChangeKeyInput() {
        currentKeyDisplay.classList.add('hidden');
        keyInputSection.classList.remove('hidden');
        apiKeyInput.focus();
    }

    function togglePasswordVisibility() {
        keyVisible = !keyVisible;
        apiKeyInput.type = keyVisible ? 'text' : 'password';
        toggleVisibility.textContent = keyVisible ? '🙈' : '👁';
    }

    async function shutdownServer() {
        if (confirm('Shutdown the server?')) {
            try {
                clearInterval(heartbeatInterval);
                await fetch('/api/shutdown', { method: 'POST' });
                showToast('Server shutting down...');
                setTimeout(() => {
                    document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:Inter,system-ui;color:#a0a0b0;background:#0d0d1a;"><div style="text-align:center;"><h2 style="color:#f4d03f;">Server stopped</h2><p>You can close this tab.</p></div></div>';
                }, 500);
            } catch (err) {
                showToast('Server stopped');
            }
        }
    }

    // Event Listeners
    searchBtn.addEventListener('click', searchTournaments);
    saveDefaultsBtn.addEventListener('click', saveDefaults);
    saveApiKeyBtn.addEventListener('click', saveApiKey);
    toggleKeySection.addEventListener('click', toggleApiKeySection);
    changeKeyBtn.addEventListener('click', showChangeKeyInput);
    toggleVisibility.addEventListener('click', togglePasswordVisibility);
    shutdownBtn.addEventListener('click', shutdownServer);

    // Enter key to save API key
    apiKeyInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') saveApiKey();
    });

    // Click to copy tag (desktop table)
    resultsBody.addEventListener('click', (e) => {
        if (e.target.classList.contains('tag-cell')) {
            copyTag(e.target.dataset.tag);
        }
    });

    // Click to copy tag (mobile cards)
    resultsCards.addEventListener('click', (e) => {
        const tagBtn = e.target.closest('.result-card-tag');
        if (tagBtn) {
            copyTag(tagBtn.dataset.tag);
        }
    });

    // Debug section toggle
    toggleDebug.addEventListener('click', () => {
        debugContent.classList.toggle('collapsed');
        toggleDebug.classList.toggle('rotated');
    });

    // Refresh logs
    refreshLogsBtn.addEventListener('click', loadLogs);

    // Tag search
    tagSearchBtn.addEventListener('click', searchTag);
    tagSearchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') searchTag();
    });

    // ===========================================
    // DEBUG FUNCTIONS
    // ===========================================

    function updateDebugStats(stats) {
        debugStats.classList.remove('hidden');

        document.getElementById('stat-queries').textContent = stats.queries || 0;
        document.getElementById('stat-drilldowns').textContent = stats.drillDowns || 0;
        document.getElementById('stat-ratelimits').textContent = stats.rateLimits || 0;
        document.getElementById('stat-errors').textContent = stats.apiErrors || 0;

        // Mode breakdown
        const modeList = document.getElementById('mode-list');
        modeList.innerHTML = '';

        if (stats.tournamentsByMode) {
            // Sort by count descending
            const sorted = Object.entries(stats.tournamentsByMode)
                .sort((a, b) => b[1] - a[1]);

            for (const [modeId, count] of sorted) {
                const modeName = gameModeNames[modeId] || `Unknown (${modeId})`;
                const div = document.createElement('div');
                div.className = 'mode-item';
                div.innerHTML = `<span class="mode-name">${escapeHtml(modeName)}</span><span class="mode-count">${count}</span>`;
                modeList.appendChild(div);
            }
        }
    }

    async function loadLogs() {
        try {
            logOutput.textContent = 'Loading...';
            const resp = await fetch('/api/logs?lines=200');
            const data = await resp.json();

            if (data.logs && data.logs.length > 0) {
                logOutput.textContent = data.logs.join('');
                // Scroll to bottom
                logOutput.scrollTop = logOutput.scrollHeight;
            } else {
                logOutput.textContent = 'No logs yet. Run a search first.';
            }
        } catch (err) {
            logOutput.textContent = 'Error loading logs: ' + err.message;
        }
    }

    async function searchTag() {
        const tag = tagSearchInput.value.trim();
        if (!tag) {
            tagSearchResult.textContent = 'Enter a tournament tag';
            tagSearchResult.className = 'tag-search-result warning';
            tagSearchResult.classList.remove('hidden');
            return;
        }

        try {
            const resp = await fetch('/api/logs?search=' + encodeURIComponent(tag));
            const data = await resp.json();

            if (data.total_matches > 0) {
                tagSearchResult.innerHTML = `<strong>Found ${data.total_matches} log entries for "${escapeHtml(tag)}"</strong><br><br>` +
                    '<pre>' + escapeHtml(data.logs.join('')) + '</pre>';
                tagSearchResult.className = 'tag-search-result success';
            } else {
                tagSearchResult.innerHTML = `<strong>No entries found for "${escapeHtml(tag)}"</strong><br>` +
                    'This tournament was NOT in the search results. Possible reasons:<br>' +
                    '- Tournament name contains unusual characters<br>' +
                    '- Tournament was created after the search<br>' +
                    '- API rate limit prevented finding it';
                tagSearchResult.className = 'tag-search-result warning';
            }
            tagSearchResult.classList.remove('hidden');
        } catch (err) {
            tagSearchResult.textContent = 'Error searching: ' + err.message;
            tagSearchResult.className = 'tag-search-result error';
            tagSearchResult.classList.remove('hidden');
        }
    }
});
