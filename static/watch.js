// Server-backed pins and Web Push. This timer only refreshes the display;
// start detection continues on the server after the PWA has been closed.
const CrWatch = (() => {
  let pins = [], config = {}, subscription = null, onChange = () => {};
  let fetching = false, busy = false, loadError = '', signature = '';
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const bounded = promise => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error('Notification setup is taking too long. Reload and retry.')), 8000))]);
  const registrationReady = () => bounded(navigator.serviceWorker.ready);
  const installed = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

  async function api(path, method = 'GET', body) {
    const response = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed. Please retry.');
    return data;
  }

  function statusText(pin) {
    const age = Math.max(0, Math.floor(Date.now() / 1000 - pin.checkedAt));
    if (pin.state === 'live') return CrTiming.deriveTiming(pin.tournament).phase === 'ended' ? 'Finished · watch completed' : 'Started · confirmed by API';
    if (pin.state === 'ended') return 'Tournament ended';
    if (pin.state === 'expired') return 'Watch expired after 24 hours';
    if (loadError || pin.failures || age > 35) return `Check delayed · last confirmed ${age}s ago`;
    return `Watching start · checked ${age}s ago`;
  }

  function render() {
    $('watch-tray').classList.toggle('hidden', pins.length === 0);
    $('watch-items').innerHTML = pins.map(pin => `
      <div class="db-watch-card ${pin.state === 'live' ? 'is-live' : ''}">
        <div class="db-watch-copy"><strong>${escape(pin.tournament.name || pin.tag)}</strong>
          <span data-watch-status="${escape(pin.tag)}">${escape(statusText(pin))}</span></div>
        ${pin.state === 'live' && CrTiming.deriveTiming(pin.tournament).phase !== 'ended' ? `<a class="db-join-btn" href="https://link.clashroyale.com/en?clashroyale://joinTournament?id=${encodeURIComponent(pin.tag.slice(1))}">JOIN</a>` : ''}
        <button type="button" class="db-icon-btn" data-unwatch="${escape(pin.tag)}" aria-label="Unpin ${escape(pin.tournament.name || pin.tag)}">✕</button>
      </div>`).join('');
    $('push-enable').hidden = !!subscription;
    $('push-disable').hidden = !subscription;
    $('push-test').hidden = !subscription;
    $('push-enable').disabled = busy;
    let hint = subscription ? 'Notifications enabled on this device. You can close the Finder.' : 'Enable notifications on this device to receive start alerts.';
    if (!config.background && config.enabled) hint += ' Local server must stay running.';
    if (!config.enabled) hint = 'Push notifications have not been configured on the server yet.';
    else if (ios && !installed()) hint = 'iPhone: Share → Add to Home Screen. Open the installed app, then enable notifications here.';
    else if (!('PushManager' in window)) hint = 'This browser does not support Web Push.';
    else if (Notification.permission === 'denied') hint = 'Notifications are blocked. Allow them in your device notification settings.';
    $('push-hint').textContent = hint;
    $('watch-push-hint').textContent = subscription ? 'Start alerts are enabled on this device.' : 'Enable start alerts in Settings to be notified when you leave.';
  }

  function accept(data) {
    pins = data.pins || [];
    const next = JSON.stringify(pins);
    render();
    if (next !== signature) { signature = next; onChange(); }
  }

  async function refresh() {
    if (fetching) return;
    fetching = true;
    try { const data = await api('/api/watches'); loadError = ''; accept(data); }
    catch (error) { loadError = error.message; render(); }
    finally { fetching = false; }
  }

  async function toggle(tag, remove = false) {
    if (busy) return;
    busy = true;
    try {
      accept(await api('/api/watches', remove ? 'DELETE' : 'POST', {tag}));
      showToast(remove ? 'Start watch removed' : subscription ? 'Pinned · you will be notified when it starts' : 'Pinned · enable notifications in Settings for start alerts');
      if (!remove && !subscription) openSettings();
    } catch (error) { showToast(error.message); await refresh(); }
    finally { busy = false; render(); }
  }

  async function enable() {
    if (busy) return;
    if (!config.enabled) { showToast('Push notifications are not configured yet.'); return; }
    if (ios && !installed()) { showToast('Add the Finder to your Home Screen first.'); return; }
    if (!('PushManager' in window) || !('Notification' in window)) { showToast('Web Push is not supported here.'); return; }
    busy = true;
    try {
      // Request permission directly from the click, before any network await (iOS).
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') throw new Error('Notifications were not enabled.');
      const registration = await registrationReady();
      const encoded = config.publicKey.replace(/-/g, '+').replace(/_/g, '/');
      const key = Uint8Array.from(atob(encoded + '='.repeat((4 - encoded.length % 4) % 4)), c => c.charCodeAt(0));
      const candidate = await bounded(registration.pushManager.getSubscription()) || await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
      await api('/api/push/subscription', 'POST', candidate.toJSON());
      subscription = candidate;
      showToast('Notifications enabled. Use Send test to check your iPhone.');
    } catch (error) { showToast(error.message); }
    finally { busy = false; render(); }
  }

  async function disable() {
    try {
      if (subscription) {
        await api('/api/push/subscription', 'DELETE', {endpoint: subscription.endpoint});
        await subscription.unsubscribe();
        subscription = null;
      }
      render();
    } catch (error) { showToast(error.message); }
  }

  function merge(tournaments) {
    const byTag = new Map(tournaments.map(t => [t.tag, {...t, watchingStart: false}]));
    pins.forEach(p => {
      const existing = byTag.get(p.tag);
      if (p.state === 'watching' || !existing || (p.state === 'live' && existing.status === 'inPreparation')) {
        byTag.set(p.tag, {...existing, ...p.tournament, watchingStart: p.state === 'watching'});
      }
    });
    return [...byTag.values()];
  }

  async function init(changed) {
    onChange = changed;
    document.addEventListener('click', event => {
      const add = event.target.closest('[data-watch]');
      const remove = event.target.closest('[data-unwatch]');
      if (add || remove) { event.stopPropagation(); toggle((add || remove).dataset[add ? 'watch' : 'unwatch'], !!remove); }
    });
    $('push-enable').addEventListener('click', enable);
    $('push-disable').addEventListener('click', disable);
    $('push-test').addEventListener('click', async () => {
      try {
        await api('/api/push/test', 'POST', {endpoint: subscription?.endpoint});
        showToast('Test sent. Check your notifications.');
      } catch (error) { showToast(error.message); }
    });
    // Pins must load even if the device push service is slow or unavailable.
    await refresh();
    try {
      config = await api('/api/push/config');
      if (config.enabled && 'serviceWorker' in navigator && 'PushManager' in window) {
        const registration = await registrationReady();
        const saved = await bounded(registration.pushManager.getSubscription());
        if (saved && Notification.permission === 'granted' && config.enabled) {
          await api('/api/push/subscription', 'POST', saved.toJSON());
          subscription = saved;
        }
      }
    } catch (error) { loadError = error.message; }
    await refresh();
    setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 10000);
    setInterval(() => pins.forEach(pin => {
      const el = document.querySelector(`[data-watch-status="${pin.tag}"]`);
      if (el) el.textContent = statusText(pin);
    }), 1000);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
  }
  return {init, merge, has: tag => pins.some(p => p.tag === tag)};
})();
