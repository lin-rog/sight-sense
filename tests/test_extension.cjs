// Exercise browser-domain minimization without launching a browser or sending data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const event = () => ({addListener() {}});
let cfg = {};
let win = {id: 1, focused: true, incognito: false, type: 'normal'};
let tab = {url: 'https://example.com/private?q=secret', incognito: false};
const sent = [];
const sandbox = {
  URL, AbortController, setTimeout, clearTimeout, setInterval() {},
  chrome: {
    storage: {local: {get: async () => cfg}, onChanged: event()},
    windows: {getLastFocused: async () => win, onFocusChanged: event()},
    tabs: {query: async () => [tab], onActivated: event(), onUpdated: event()},
    action: {setBadgeText: async () => {}, onClicked: event()},
    runtime: {onInstalled: event(), onStartup: event()},
    alarms: {onAlarm: event(), create() {}},
  },
  fetch: async (url, request) => {sent.push({url, body: JSON.parse(request.body)}); return {ok: true};},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../browser-extension/background.js'), 'utf8')
                + '\nglobalThis.testHeartbeat = heartbeat;', sandbox);
(async () => {
  await new Promise(setImmediate);
  cfg = {enabled: true, token: 'x'.repeat(32), browserApp: 'chrome.exe'};
  await sandbox.testHeartbeat();
  assert.equal(sent.at(-1).body.domain, 'example.com');
  assert.equal(JSON.stringify(sent).includes('secret'), false);
  win.focused = false;
  await sandbox.testHeartbeat();
  assert.equal(sent.at(-1).body.domain, null);
  win.focused = true;
  tab.incognito = true;
  await sandbox.testHeartbeat();
  assert.equal(sent.at(-1).body.domain, null);
  tab.incognito = false;
  tab.url = 'chrome://settings';
  await sandbox.testHeartbeat();
  assert.equal(sent.at(-1).body.domain, null);
  tab.url = 'https://example.com/';
  cfg.enabled = false;
  await sandbox.testHeartbeat();
  assert.equal(sent.at(-1).body.domain, null);
  console.log('Extension checks passed: domain only, focused normal tab, incognito excluded, opt-in required.');
})().catch(error => {console.error(error); process.exitCode = 1;});
