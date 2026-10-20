// Creator codes, password mentions and channels in tournament names/descriptions.
// Shared by the browser and Node-based tests.
var CrHints = (() => {
  // Words that follow "pass"/"code"/"twitch" in prose but are never the value itself.
  const STOPWORDS = new Set([
    'in', 'en', 'on', 'at', 'my', 'mi', 'is', 'es', 'the', 'el', 'la', 'de', 'do', 'da', 'del',
    'para', 'for', 'to', 'a', 'y', 'and', 'no', 'live', 'royale', 'tournament', 'torneo',
  ]);
  const TOKEN = '([\\p{L}\\p{N}_-]{2,24})';
  const HANDLE = '@?([\\p{L}\\p{N}_.]{3,30})';
  const END = '(?![\\p{L}\\p{N}])';  // "code" must be a whole word, not the start of "codeoj"
  const PATTERNS = [
    // "Pass = 98CR", "contraseña: princesa"
    { kind: 'password', re: new RegExp(`(?:^|[^\\p{L}])(?:pass(?:word)?|pwd?|contrase[ñn]a|senha|clave|mdp)${END}\\s*(?:[:=]|is\\b|es\\b)?\\s*["“']?${TOKEN}`, 'giu') },
    // "CODIGO: MAWI", "Code 98CR"
    { kind: 'code', re: new RegExp(`(?:^|[^\\p{L}])c[oó]d(?:e|igo)${END}\\s*[:=]?\\s*${TOKEN}`, 'giu') },
    // "No olvides poner dark en la tienda"
    { kind: 'code', re: new RegExp(`(?:^|[^\\p{L}])(?:poner|pon|usa|usen|use)\\s+${TOKEN}\\s+(?:en\\s+la\\s+tienda|in\\s+(?:the\\s+)?shop)`, 'giu') },
    // "TG:@RoyalFamily", "X : SavageLavaOP"
    { kind: 'channel', re: new RegExp(`(?:^|[^\\p{L}])(twitch|tiktok|youtube|yt|tg|telegram|instagram|ig|kick|x)\\s*[:=]\\s*${HANDLE}`, 'giu') },
    // "TWITCH ZODIAC_CR"
    { kind: 'channel', re: new RegExp(`(?:^|[^\\p{L}])(twitch|tiktok|youtube|kick)\\s+${HANDLE}`, 'giu') },
    { kind: 'channel', re: /()@([\p{L}\p{N}_.]{3,30})/gu },
  ];
  const PROFILE_URLS = {
    twitch: v => `https://www.twitch.tv/${v}`,
    tiktok: v => `https://www.tiktok.com/@${v}`,
    youtube: v => `https://www.youtube.com/@${v}`,
    yt: v => `https://www.youtube.com/@${v}`,
    tg: v => `https://t.me/${v}`,
    telegram: v => `https://t.me/${v}`,
    x: v => `https://x.com/${v}`,
    instagram: v => `https://www.instagram.com/${v}`,
    ig: v => `https://www.instagram.com/${v}`,
    kick: v => `https://kick.com/${v}`,
  };
  const PLATFORM_NAMES = {
    twitch: 'Twitch', tiktok: 'TikTok', youtube: 'YouTube', yt: 'YouTube', tg: 'Telegram',
    telegram: 'Telegram', x: 'X', instagram: 'Instagram', ig: 'Instagram', kick: 'Kick',
  };
  const RANK = { password: 0, code: 1, channel: 2 };

  function extract(...texts) {
    const hints = [];
    for (const text of texts) {
      if (!text) continue;
      for (const { kind, re } of PATTERNS) {
        re.lastIndex = 0;
        for (const match of String(text).matchAll(re)) {
          const platform = kind === 'channel' ? (match[1] || '').toLowerCase() : '';
          const value = (kind === 'channel' ? match[2] : match[1]).replace(/[._-]+$/, '');
          if (value.length < 2 || STOPWORDS.has(value.toLowerCase())) continue;
          add(hints, { kind, value, platform });
        }
      }
    }
    return hints.sort((a, b) => RANK[a.kind] - RANK[b.kind]).slice(0, 4);
  }

  // One hint per value; a truncated copy (name cut at 32 chars) yields to the longer one.
  function add(hints, hint) {
    const key = hint.value.toLowerCase();
    const same = hints.find(h => h.kind === hint.kind && (h.value.toLowerCase().startsWith(key) || key.startsWith(h.value.toLowerCase())));
    if (!same) { hints.push(hint); return; }
    if (hint.value.length > same.value.length) same.value = hint.value;
    if (!same.platform) same.platform = hint.platform;
  }

  function links(hint) {
    const query = hint.kind === 'code' ? `${hint.value} clash royale` : hint.value;
    const q = encodeURIComponent(query);
    const result = [];
    const profile = PROFILE_URLS[hint.platform];
    if (profile) result.push({ label: PLATFORM_NAMES[hint.platform], url: profile(encodeURIComponent(hint.value)), profile: true });
    result.push(
      { label: 'TikTok', url: `https://www.tiktok.com/search?q=${q}` },
      { label: 'YouTube', url: `https://www.youtube.com/results?search_query=${q}` },
      { label: 'Twitch', url: `https://www.twitch.tv/search?term=${q}` },
    );
    return result;
  }

  return { extract, links, platformName: platform => PLATFORM_NAMES[platform] || '' };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = CrHints;
}
