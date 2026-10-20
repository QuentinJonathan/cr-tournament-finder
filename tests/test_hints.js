const test = require('node:test');
const assert = require('node:assert/strict');

const { extract, links } = require('../static/hints.js');

const pick = hints => hints.map(h => `${h.kind}:${h.value}${h.platform ? '@' + h.platform : ''}`);

// Real names/descriptions of creator tournaments seen in October 2026.
test('password and code mentions are extracted', () => {
  assert.deepEqual(pick(extract('Princess Emote #3 - Code 98CR', '#1 gets emote if 100+ players. Pass = 98CR')),
    ['password:98CR', 'code:98CR']);
  assert.deepEqual(pick(extract('🏆1 ➡️ EMOTE PRINCESA 2', 'El primero gana el emote automáticamente luego de finalizar el torneo. Suerte 🍀 si quieren ayudar 👉 CODIGO: MAWI 😳 Únete a mi grupo de Whatsapp para más torneos oficiales 🤠')),
    ['code:MAWI']);
  assert.deepEqual(pick(extract('CODIGO DARK', 'Emote de princesa al ganador! No olvides poner dark en la tienda 🔥')),
    ['code:DARK']);
  // The password itself starts with "code"; only the PW keyword may match.
  assert.deepEqual(pick(extract('Princess Tournament: PW: codeoj', '')), ['password:codeoj']);
});

test('channels keep the platform and the untruncated handle', () => {
  assert.deepEqual(pick(extract('Emote 21 TG:@RoyalFamilyNewsCha', 'Password in TG:@RoyalFamilyNewsChannel')),
    ['channel:RoyalFamilyNewsChannel@tg']);
  assert.deepEqual(pick(extract('Savage Love', 'Top 1 Princess Emote 😍 Follow Me For more Tournament / X : SavageLavaOP / YT : S')),
    ['channel:SavageLavaOP@x']);
  assert.deepEqual(pick(extract('TWITCH ZODIAC_CR', 'top 1 gets emote')), ['channel:ZODIAC_CR@twitch']);
});

test('ordinary names and prose produce no hints', () => {
  for (const [name, description] of [
    ['Pass Royale', ''],
    ['Torneo Emote princesa', 'El primero se lleva el emote'],
    ['ميسي', 'live in tiktok'],
    ['Torneo x ganador', 'que vença o melhor'],
    ['bakeer', 'سلمو  7md'],
  ]) {
    assert.deepEqual(extract(name, description), [], `${name} / ${description}`);
  }
});

test('links search the value and open known profiles directly', () => {
  const [tg] = extract('', 'TG:@RoyalFamilyNewsChannel');
  assert.deepEqual(links(tg).map(l => l.label), ['Telegram', 'TikTok', 'YouTube', 'Twitch']);
  assert.equal(links(tg)[0].url, 'https://t.me/RoyalFamilyNewsChannel');
  const [code] = extract('', 'CODIGO: MAWI');
  assert.equal(links(code)[0].url, 'https://www.tiktok.com/search?q=MAWI%20clash%20royale');
});
