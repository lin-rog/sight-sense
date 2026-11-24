const token = document.querySelector('#token');
const browserApp = document.querySelector('#browserApp');
const enabled = document.querySelector('#enabled');
chrome.storage.local.get(['token', 'browserApp', 'enabled']).then(cfg => {
  token.value = cfg.token || '';
  browserApp.value = cfg.browserApp || (navigator.userAgent.includes('Edg/') ? 'msedge.exe' : 'chrome.exe');
  enabled.checked = Boolean(cfg.enabled);
});
document.querySelector('#save').addEventListener('click', async () => {
  if (enabled.checked && token.value.trim().length < 32) {
    document.querySelector('#status').textContent = '请填写采集器生成的配对令牌。';
    return;
  }
  await chrome.storage.local.set({token: token.value.trim(), browserApp: browserApp.value, enabled: enabled.checked});
  document.querySelector('#status').textContent = '设置已保存。';
});
