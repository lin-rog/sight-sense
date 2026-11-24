"""Local activity ledger. No window titles, URLs or page contents are stored."""
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BROWSER_APPS = {'chrome.exe', 'msedge.exe', 'brave.exe', 'firefox.exe', 'vivaldi.exe', 'opera.exe'}


def domain_name(value):
    value = str(value).lower().rstrip('.')
    if len(value) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
        raise ValueError('invalid domain')
    if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-')
           for label in value.split('.')):
        raise ValueError('invalid domain')
    return value


def process_name(value):
    value = str(value).lower()
    if not re.fullmatch(r'[\w. +()-]{1,100}\.exe', value):
        raise ValueError('invalid executable name')
    return value


class Ledger:
    def __init__(self, path, create=False):
        self.path = Path(path)
        if not create and not self.path.is_file():
            raise FileNotFoundError(self.path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS intervals (
                        start REAL NOT NULL, end REAL NOT NULL,
                        app TEXT NOT NULL, domain TEXT);
                    CREATE INDEX IF NOT EXISTS intervals_time ON intervals(end);
                    CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT OR IGNORE INTO control VALUES ('paused', 'false');
                    INSERT OR IGNORE INTO control VALUES ('generation', '0');
                    CREATE TABLE IF NOT EXISTS deliveries (
                        id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL);
                ''')

    @contextmanager
    def connect(self):
        # mode=rw prevents a missing Docker mount silently creating an empty ledger.
        uri = self.path.resolve().as_uri() + ('?mode=rw' if self.path.exists() else '?mode=rwc')
        if not self.path.parent.exists():
            raise FileNotFoundError(self.path)
        db = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            db.execute('PRAGMA secure_delete=ON')
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM control WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO control VALUES (?,?)', (key, json.dumps(value)))

    def append(self, start, end, app, domain=None, generation=None):
        if not all(math.isfinite(t) for t in (start, end)) or not 0 < end - start <= 60:
            raise ValueError('invalid interval')
        app = process_name(app)
        domain = domain_name(domain) if domain else None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            epoch = db.execute("SELECT value FROM control WHERE key='generation'").fetchone()
            if generation is not None and int(epoch[0]) != generation:
                return
            paused = db.execute("SELECT value FROM control WHERE key='paused'").fetchone()
            if paused and json.loads(paused[0]):
                return
            last = db.execute('SELECT MAX(end) FROM intervals').fetchone()[0]
            start = max(start, last) if last is not None else start
            if start < end:
                db.execute('INSERT INTO intervals VALUES (?,?,?,?)', (start, end, app, domain))

    def totals(self, start, end):
        apps, domains = {}, {}
        with self.connect() as db:
            rows = db.execute('SELECT start,end,app,domain FROM intervals WHERE end>? AND start<?',
                              (start, end)).fetchall()
        for left, right, app, domain in rows:
            seconds = max(0, min(right, end) - max(left, start))
            apps[app] = apps.get(app, 0) + seconds
            if domain:
                domains[domain] = domains.get(domain, 0) + seconds
        return {'app': apps, 'domain': domains}

    def prune(self, now, days=30):
        with self.connect() as db:
            cutoff = now - days * 86400
            db.execute('DELETE FROM intervals WHERE end<?', (cutoff,))
            db.execute('DELETE FROM deliveries WHERE created<?', (cutoff,))

    def clear(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM intervals')
            db.execute('DELETE FROM deliveries')
            db.execute("UPDATE control SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) WHERE key='generation'")

    def reserve(self, identity, now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            generation = db.execute("SELECT value FROM control WHERE key='generation'").fetchone()[0]
            paused = db.execute("SELECT value FROM control WHERE key='paused'").fetchone()[0]
            if str(identity).split(':', 1)[0] != generation or json.loads(paused):
                return False
            return db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?)',
                              (identity, now, 'reserved')).rowcount == 1

    def delivered(self, identity, status):
        with self.connect() as db:
            db.execute('UPDATE deliveries SET status=? WHERE id=?', (status, identity))


def day_bounds(now, tz):
    local = datetime.fromtimestamp(now, ZoneInfo(tz))
    left = datetime.combine(local.date(), time.min, local.tzinfo)
    right = datetime.combine(local.date() + timedelta(days=1), time.min, local.tzinfo)
    return local.date().isoformat(), left.timestamp(), right.timestamp()


def duration(seconds):
    return f'{seconds / 3600:.2f} 小时（{int(seconds / 60)} 分钟）'


def summary(ledger, now, tz='Asia/Shanghai', days=1):
    date, left, _ = day_bounds(now, tz)
    if days > 1:
        local = datetime.fromtimestamp(left, ZoneInfo(tz)) - timedelta(days=days - 1)
        left = local.timestamp()
    totals = ledger.totals(left, now)
    lines = [f'视感使用日志 | 截至 {datetime.fromtimestamp(now, ZoneInfo(tz)):%Y-%m-%d %H:%M} | 最近 {days} 天',
             '口径：前台活跃时长；离开电脑、暂停及未采集时段不计。软件与网站时长有重叠。']
    for kind, label in [('app', '软件'), ('domain', '网站域名')]:
        top = sorted(totals[kind].items(), key=lambda x: (-x[1], x[0]))[:10]
        lines.append(label + '：' + ('；'.join(f'{name} {duration(sec)}' for name, sec in top) or '暂无记录'))
    lines.append('以上仅为使用事实，不代表兴趣、性格或心理判断；画像分析尚未启用。')
    return '\n'.join(lines)


def threshold_jobs(ledger, now, cfg):
    date, left, _ = day_bounds(now, cfg.get('timezone') or 'Asia/Shanghai')
    totals = ledger.totals(left, now)
    generation = ledger.get('generation', 0)
    result = []
    app_threshold = float(cfg.get('app_threshold_minutes', 90)) * 60
    browser_threshold = float(cfg.get('browser_threshold_minutes', 60)) * 60
    if any(not math.isfinite(t) or t <= 0 for t in (app_threshold, browser_threshold)):
        raise ValueError('threshold must be positive')
    for name, seconds in sorted(totals['app'].items()):
        if name not in BROWSER_APPS and seconds >= app_threshold:
            result.append((f'{generation}:{date}:app:{name}',
                           f'视感 · {date}\n当天累计使用软件 {name} {duration(seconds)}，'
                           f'已达到 {int(app_threshold / 60)} 分钟阈值。'))
    browser_seconds = sum(sec for app, sec in totals['app'].items() if app in BROWSER_APPS)
    if browser_seconds >= browser_threshold:
        sites = sorted(totals['domain'].items(), key=lambda x: (-x[1], x[0]))[:10]
        details = '；'.join(f'{name} {duration(sec)}' for name, sec in sites)
        result.append((f'{generation}:{date}:browser:all',
                       f'视感 · {date}\n当天浏览器累计使用 {duration(browser_seconds)}，'
                       f'已达到 {int(browser_threshold / 60)} 分钟阈值。\n网站域名：'
                       + (details or '暂无有效记录（可能未启用扩展）。')
                       + '\n网站时长仅覆盖有有效域名观测的时段。'))
    return result


class Sampler:
    """Credit only intervals bracketed by compatible active observations."""
    def __init__(self, ledger, interval=5):
        self.ledger = ledger
        self.interval = interval
        self.previous = None

    def tick(self, now, monotonic, app, idle, domain=None):
        active = bool(app) and not idle and not self.ledger.get('paused', False)
        generation = self.ledger.get('generation', 0)
        current = (now, monotonic, app, domain, generation) if active else None
        old, self.previous = self.previous, current
        if not old or not current:
            return
        elapsed = monotonic - old[1]
        if (old[2] != app or old[4] != generation or not 0 < elapsed <= min(60, self.interval * 2.5)
                or abs(now - old[0] - elapsed) > 1):
            return
        # Domain switches/expired browser heartbeat never inherit the previous tab.
        matched_domain = domain if old[3] == domain else None
        self.ledger.append(old[0], now, app, matched_domain, generation=generation)
