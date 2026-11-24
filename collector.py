"""Visible, explicitly started Windows collector and loopback browser bridge."""
import argparse
import ctypes
from ctypes import wintypes
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time

from core import BROWSER_APPS, Ledger, Sampler, domain_name

ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger('sight-sense')


class WindowsProbe:
    def __init__(self):
        if os.name != 'nt':
            raise RuntimeError('The collector requires an interactive Windows desktop')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                         wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.GetTickCount.restype = wintypes.DWORD
        self.user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.user.OpenInputDesktop.restype = wintypes.HANDLE
        self.user.SwitchDesktop.argtypes = [wintypes.HANDLE]
        self.user.CloseDesktop.argtypes = [wintypes.HANDLE]

        class LastInput(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]
        self.LastInput = LastInput
        self.user.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInput)]

    def read(self, idle_seconds):
        # A locked/secure desktop is unavailable for switching: do not count it.
        desktop = self.user.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            return None, True
        try:
            if not self.user.SwitchDesktop(desktop):
                return None, True
        finally:
            self.user.CloseDesktop(desktop)
        last = self.LastInput()
        last.cbSize = ctypes.sizeof(last)
        if not self.user.GetLastInputInfo(ctypes.byref(last)):
            return None, True
        idle = ((self.kernel.GetTickCount() - last.dwTime) & 0xFFFFFFFF) / 1000 >= idle_seconds
        hwnd = self.user.GetForegroundWindow()
        if idle or not hwnd:
            return None, True
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None, True
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None, True
            return Path(buffer.value).name.lower(), False
        finally:
            self.kernel.CloseHandle(handle)


class BrowserBridge:
    def __init__(self, token, allowed=True, excluded=()):
        self.token = token
        self.allowed = allowed
        self.excluded = tuple(excluded)
        self.lock = threading.Lock()
        self.tabs = {}

    def accept(self, payload, now):
        app = payload.get('browser_app')
        if app not in BROWSER_APPS:
            raise ValueError('unsupported browser')
        domain = domain_name(payload['domain']) if payload.get('domain') else None
        if domain and any(domain == item or domain.endswith('.' + item) for item in self.excluded):
            domain = None
        with self.lock:
            if self.allowed:
                self.tabs[app] = (domain, now)

    def current(self, app, now):
        with self.lock:
            domain, received = self.tabs.get(app, (None, 0))
            return domain if self.allowed and 0 <= now - received <= 12 else None

    def reset(self):
        with self.lock:
            self.tabs.clear()


def bridge_server(bridge, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SightSense'

        def log_message(self, *args):
            pass  # Never log domains or authentication headers.

        def origin(self):
            return bool(re.fullmatch(r'chrome-extension://[a-p]{32}', self.headers.get('Origin', '')))

        def reply(self, code):
            self.send_response(code)
            if self.origin():
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Sight-Token')
                self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_OPTIONS(self):
            self.reply(204 if self.origin() and self.path == '/v1/tab' else 403)

        def do_POST(self):
            self.connection.settimeout(3)
            if self.path != '/v1/tab':
                self.reply(404)
                return
            if not self.origin() or not hmac.compare_digest(self.headers.get('X-Sight-Token', ''), bridge.token):
                self.reply(403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024:
                    raise ValueError('body size')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('body type')
                bridge.accept(payload, time.monotonic())
            except (ValueError, TypeError, KeyError, OSError):
                self.reply(400)
                return
            self.reply(204)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def load_config(path):
    config = json.loads(path.read_text(encoding='utf-8'))
    if len(config.get('browser_token', '')) < 32:
        raise ValueError('Run init to generate a browser token')
    for key, low, high in [('sample_seconds', 1, 20), ('idle_seconds', 30, 3600),
                           ('retention_days', 1, 365), ('browser_port', 1024, 65535)]:
        if not low <= int(config[key]) <= high:
            raise ValueError(f'invalid {key}')
    config['excluded_domains'] = [domain_name(d) for d in config.get('excluded_domains', [])]
    return config


def run(config, consent):
    if not consent:
        raise ValueError('需要本人明确启动：run --consent；不会安装自启动或隐藏进程。')
    ledger = Ledger(config['database_path'], create=True)
    probe = WindowsProbe()
    sampler = Sampler(ledger, int(config['sample_seconds']))
    bridge = BrowserBridge(config['browser_token'], config.get('collect_domains', False),
                           config.get('excluded_domains', []))
    server = bridge_server(bridge, int(config['browser_port']))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print('视感采集器运行中：仅前台软件和已启用的网站域名。Ctrl+C 停止。')
    print('网站记录：' + ('开启' if bridge.allowed else '关闭') + '；暂停状态：' + str(ledger.get('paused', False)))
    last_prune = 0
    generation = ledger.get('generation', 0)
    try:
        while True:
            now, mono = time.time(), time.monotonic()
            if now - last_prune > 3600:
                ledger.prune(now, int(config['retention_days']))
                last_prune = now
            fresh_generation = ledger.get('generation', 0)
            if fresh_generation != generation or ledger.get('paused', False):
                sampler.previous = None
                bridge.reset()
                generation = fresh_generation
            app, idle = probe.read(int(config['idle_seconds']))
            if app in set(config.get('excluded_apps', [])):
                app = None
            sampler.tick(now, mono, app, idle, bridge.current(app, mono))
            ledger.set('heartbeat', now)
            time.sleep(int(config['sample_seconds']))
    except KeyboardInterrupt:
        print('\n视感采集已停止。')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'collector-config.json')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='create private local config; does not start collecting')
    runner = commands.add_parser('run', help='start an interactive collector')
    runner.add_argument('--consent', action='store_true')
    for name in ('status', 'pause', 'resume', 'clear'):
        commands.add_parser(name)
    args = parser.parse_args()
    if args.command == 'init':
        config = {'database_path': str(ROOT / 'data' / 'activity.sqlite3'),
                  'sample_seconds': 5, 'idle_seconds': 300, 'retention_days': 30,
                  'collect_domains': False, 'browser_port': 19876,
                  'browser_token': secrets.token_urlsafe(32),
                  'excluded_apps': [], 'excluded_domains': []}
        # Exclusive creation preserves an existing token and user settings.
        with args.config.open('x', encoding='utf-8') as file:
            json.dump(config, file, ensure_ascii=False, indent=2)
        Ledger(config['database_path'], create=True)
        print(f'已生成 {args.config}。尚未开始采集；令牌只需填入本人浏览器扩展设置。')
        return
    config = load_config(args.config)
    if args.command == 'run':
        run(config, args.consent)
        return
    ledger = Ledger(config['database_path'])
    if args.command in ('pause', 'resume'):
        ledger.set('paused', args.command == 'pause')
    elif args.command == 'clear':
        ledger.clear()
    print(json.dumps({'paused': ledger.get('paused', False),
                      'last_collector_heartbeat': ledger.get('heartbeat'),
                      'operation': args.command}, ensure_ascii=False))


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
