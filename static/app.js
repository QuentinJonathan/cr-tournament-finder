// Clash Royale Tournament Finder - Frontend Logic

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
    const statusMessage = document.getElementById('status-message');
    const resultsSection = document.getElementById('results-section');
    const resultsCount = document.getElementById('results-count');
    const resultsBody = document.getElementById('results-body');
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
    const gameModeSelect = document.getElementById('game-mode');
    const levelCapSelect = document.getElementById('level-cap');
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
        await loadConfig();
        await loadGameModes();
        startHeartbeat();
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

            if (hasApiKey && data.masked_key) {
                // Show current key info, hide input
                currentKeyDisplay.classList.remove('hidden');
                keyInputSection.classList.add('hidden');
                maskedKey.textContent = data.masked_key;
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
            gameModeSelect.innerHTML = '';
            for (const [id, name] of Object.entries(modes)) {
                const option = document.createElement('option');
                option.value = id;
                option.textContent = name;
                gameModeSelect.appendChild(option);
            }
        } catch (err) {
            console.error('Failed to load game modes:', err);
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
        if (filters.game_modes && filters.game_modes.length) {
            Array.from(gameModeSelect.options).forEach(opt => {
                opt.selected = filters.game_modes.includes(opt.value);
            });
        }
        if (filters.level_caps && filters.level_caps.length) {
            Array.from(levelCapSelect.options).forEach(opt => {
                opt.selected = filters.level_caps.includes(opt.value);
            });
        }
    }

    function getFilters() {
        return {
            tournament_type: tournamentType.value,
            status: statusFilter.value,
            game_modes: Array.from(gameModeSelect.selectedOptions).map(o => o.value),
            level_caps: Array.from(levelCapSelect.selectedOptions).map(o => o.value),
            min_players: parseInt(minPlayers.value) || 0,
            max_players: parseInt(maxPlayers.value) || null,
            min_remaining_minutes: parseInt(minRemaining.value) || 0,
            max_remaining_minutes: parseInt(maxRemaining.value) || null
        };
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
        setTimeout(() => {
            toast.classList.add('hidden');
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
        showStatus('Fetching tournaments... This may take a few seconds.', 'loading');

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
            resultsSection.classList.remove('hidden');
            resultsCount.textContent = '';
            return;
        }

        resultsCount.textContent = `(${tournaments.length})`;
        resultsBody.innerHTML = '';

        tournaments.forEach(t => {
            const tr = document.createElement('tr');

            // Format remaining time
            let timeLeft = '—';
            if (t.remainingMinutes !== null) {
                if (t.remainingMinutes < 60) {
                    timeLeft = `${t.remainingMinutes} min`;
                } else {
                    const hours = Math.floor(t.remainingMinutes / 60);
                    const mins = t.remainingMinutes % 60;
                    timeLeft = `${hours}h ${mins}m`;
                }
            }

            // Format elapsed time (running for)
            let elapsed = '—';
            if (t.elapsedMinutes !== null && t.elapsedMinutes !== undefined) {
                if (t.elapsedMinutes < 60) {
                    elapsed = `${t.elapsedMinutes} min`;
                } else {
                    const hours = Math.floor(t.elapsedMinutes / 60);
                    const mins = t.elapsedMinutes % 60;
                    elapsed = `${hours}h ${mins}m`;
                }
            }

            // Status badge
            const statusClass = t.status === 'inProgress' ? 'in-progress' : 'in-preparation';
            const statusText = t.status === 'inProgress' ? 'In Progress' : 'Preparation';

            tr.innerHTML = `
                <td>${escapeHtml(t.name)}</td>
                <td>${escapeHtml(t.gameMode)}</td>
                <td>${t.players}/${t.maxPlayers}</td>
                <td>${t.levelCap}</td>
                <td><span class="status-badge ${statusClass}">${statusText}</span></td>
                <td>${elapsed}</td>
                <td>${timeLeft}</td>
                <td class="tag-cell" data-tag="${escapeHtml(t.tag)}">${escapeHtml(t.tag)}</td>
            `;

            resultsBody.appendChild(tr);
        });

        resultsSection.classList.remove('hidden');
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
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
                    document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui;color:#666;"><div style="text-align:center;"><h2>Server stopped</h2><p>You can close this tab.</p></div></div>';
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

    // Click to copy tag
    resultsBody.addEventListener('click', (e) => {
        if (e.target.classList.contains('tag-cell')) {
            copyTag(e.target.dataset.tag);
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
