const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('saved pins load even when browser push registration never resolves', async () => {
  const elements = new Map(), calls = [];
  const element = () => ({classList:{toggle(){}},addEventListener(){},textContent:'',innerHTML:''});
  const pins = [{id:'one',tag:'#2PQGYYGY',state:'watching',checkedAt:Date.now()/1000,tournament:{name:'Pinned'}}];
  const context = {navigator:{userAgent:'test',platform:'test',serviceWorker:{ready:new Promise(()=>{})}},
    window:{PushManager:function(){}},Notification:{permission:'default'},matchMedia:()=>({matches:false}),
    document:{getElementById:id=>{if(!elements.has(id)) elements.set(id,element());return elements.get(id);},addEventListener(){}},
    fetch: async path => {calls.push(path);return {ok:true,json:async()=>path==='/api/watches'?{pins}:{enabled:true,background:true}};},
    setInterval(){},setTimeout(){},URLSearchParams,Promise,Map,Uint8Array,console};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/watch.js','utf8')+'\nglobalThis.watches = CrWatch;',context);
  let loaded = false;
  context.watches.init(()=>{loaded=true;});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(loaded,true);
  assert.equal(context.watches.has('#2PQGYYGY'),true);
  assert.ok(calls.includes('/api/watches'));
  assert.match(elements.get('watch-items').innerHTML,/Pinned/);
});
