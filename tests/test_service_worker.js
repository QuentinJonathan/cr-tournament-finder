const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

function worker() {
  const handlers = {}, shown = [], opened = [];
  const context = {URL, console, clients: {openWindow: async url => opened.push(url)},
    self: {location: {origin:'https://finder.example'}, addEventListener:(name, handler) => handlers[name] = handler,
      registration:{showNotification: async (title, options) => shown.push({title, options})}}};
  vm.runInNewContext(fs.readFileSync('static/service-worker.js','utf8'), context);
  return {handlers, shown, opened};
}

test('background push displays a notification with a stable tag and tournament link', async () => {
  const {handlers, shown} = worker(); let pending;
  handlers.push({data:{json:()=>({title:'Started', body:'Join now', tag:'cr-start-1',url:'/?tournament=2PQGYYGY'})},waitUntil:p=>pending=p});
  await pending;
  assert.equal(shown[0].title, 'Started');
  assert.equal(shown[0].options.tag, 'cr-start-1');
  assert.equal(shown[0].options.data.url, '/?tournament=2PQGYYGY');
});

test('notification click opens the tournament in the PWA and rejects external redirect URLs', async () => {
  const {handlers, opened} = worker();
  for (const url of ['/?tournament=2PQGYYGY', 'https://external.example/']) {
    let pending;
    handlers.notificationclick({notification:{data:{url},close(){}},waitUntil:p=>pending=p});
    await pending;
  }
  assert.deepEqual(opened, ['https://finder.example/?tournament=2PQGYYGY', 'https://finder.example/']);
});
