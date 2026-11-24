// No page scripts, titles, history, full URLs or content leave the browser.
let sending = false;
let pending = false;
let currentController = null;

async function heartbeat() {
  if (sending) {
    pending = true;
    currentController?.abort(); // A focus change invalidates an in-flight tab observation.
    return;
  }
  sending = true;
  try {
    const cfg = await chrome.storage.local.get(['enabled', 'token', 'browserApp']);
    if (!cfg.token) return;
    let domain = null;
    if (cfg.enabled) {
      const win = await chrome.windows.getLastFocused();
      if (win.focused && !win.incognito && win.type === 'normal') {
        const [tab] = await chrome.tabs.query({active: true, windowId: win.id});
        if (tab && !tab.incognito && tab.url) {
          const url = new URL(tab.url);
          if (['https:', 'http:'].includes(url.protocol)) domain = url.hostname;
        }
      }
    }
    const controller = new AbortController();
    currentController = controller;
    const timer = setTimeout(() => controller.abort(), 2000);
    try {
      const response = await fetch('http://127.0.0.1:19876/v1/tab', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Sight-Token': cfg.token},
        body: JSON.stringify({domain, browser_app: cfg.browserApp || 'chrome.exe'}),
        signal: controller.signal,
      });
      await chrome.action.setBadgeText({text: response.ok && cfg.enabled ? 'ON' : '!'});
    } finally {
      clearTimeout(timer);
    }
  } catch {
    await chrome.action.setBadgeText({text: '!'});
  } finally {
    sending = false;
    currentController = null;
    if (pending) {
      pending = false;
      void heartbeat();
    }
  }
}

chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.tabs.onActivated.addListener(heartbeat);
chrome.tabs.onUpdated.addListener((_id, change) => {
  if (change.url || change.status === 'complete') void heartbeat();
});
chrome.windows.onFocusChanged.addListener(heartbeat);
chrome.storage.onChanged.addListener(heartbeat);
chrome.alarms.onAlarm.addListener(heartbeat);
chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create('sight-heartbeat', {periodInMinutes: 0.5});
  void heartbeat();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('sight-heartbeat', {periodInMinutes: 0.5});
  void heartbeat();
});
// Requests/events keep the MV3 worker active while paired with a running collector.
// A worker suspension expires the server heartbeat in 12 seconds; never guess a tab.
setInterval(heartbeat, 5000);
void heartbeat();
