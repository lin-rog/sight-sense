import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import Ledger, Sampler, day_bounds, domain_name, summary, threshold_jobs
from collector import BrowserBridge


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'activity.sqlite3'
        self.ledger = Ledger(self.path, create=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_path_does_not_create_a_database(self):
        missing = Path(self.tmp.name) / 'missing.sqlite3'
        with self.assertRaises(FileNotFoundError):
            Ledger(missing)
        self.assertFalse(missing.exists())

    def test_no_overlap_or_double_count_after_restart(self):
        self.ledger.append(100, 105, 'chrome.exe', 'example.com')
        Ledger(self.path).append(103, 108, 'chrome.exe', 'example.com')
        self.assertEqual(self.ledger.totals(0, 200),
                         {'app': {'chrome.exe': 8}, 'domain': {'example.com': 8}})

    def test_software_and_domain_thresholds_once_per_day(self):
        now = datetime(2026, 10, 17, 12, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        for i in range(90):
            self.ledger.append(now - 5400 + i * 60, now - 5340 + i * 60,
                               'code.exe')
        for i in range(60):
            self.ledger.append(now + i * 60, now + 60 + i * 60,
                               'chrome.exe', 'example.com' if i < 30 else 'other.com')
        now += 3600
        jobs = threshold_jobs(self.ledger, now, {})
        self.assertEqual(len(jobs), 2)
        browser_report = next(text for _, text in jobs if '浏览器累计' in text)
        self.assertIn('example.com', browser_report)
        self.assertIn('other.com', browser_report)
        for identity, _ in jobs:
            self.assertTrue(self.ledger.reserve(identity, now))
            self.assertFalse(self.ledger.reserve(identity, now))
            self.assertFalse(Ledger(self.path).reserve(identity, now))
        self.assertEqual(threshold_jobs(self.ledger, now + 86400, {}), [])

    def test_midnight_split_and_dst_bounds(self):
        midnight = datetime(2026, 10, 17, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        self.ledger.append(midnight - 3, midnight + 2, 'code.exe')
        self.assertEqual(self.ledger.totals(midnight, midnight + 100)['app']['code.exe'], 2)
        self.assertEqual(self.ledger.totals(midnight - 100, midnight)['app']['code.exe'], 3)
        fall = datetime(2026, 11, 1, 12, tzinfo=ZoneInfo('America/New_York')).timestamp()
        _, start, end = day_bounds(fall, 'America/New_York')
        self.assertEqual(end - start, 25 * 3600)

    def test_pause_clear_invalidates_prepared_writes_and_jobs(self):
        self.ledger.append(100, 105, 'code.exe')
        self.ledger.set('paused', True)
        self.ledger.append(105, 110, 'code.exe')
        self.assertEqual(self.ledger.totals(0, 200)['app']['code.exe'], 5)
        self.assertFalse(self.ledger.reserve('0:day:app:code.exe', 110))
        self.ledger.set('paused', False)
        self.ledger.clear()
        self.ledger.append(105, 110, 'code.exe', generation=0)
        self.assertEqual(self.ledger.totals(0, 200)['app'], {})
        self.assertFalse(self.ledger.reserve('0:day:app:code.exe', 110))
        self.assertTrue(self.ledger.reserve('1:day:app:code.exe', 110))

    def test_prune_and_descriptive_summary(self):
        self.ledger.append(0, 5, 'old.exe')
        now = datetime(2026, 10, 17, 12, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
        self.ledger.append(now - 5, now, 'code.exe')
        self.ledger.prune(now, 30)
        self.assertNotIn('old.exe', str(self.ledger.totals(0, now)))
        report = summary(self.ledger, now)
        self.assertIn('code.exe', report)
        self.assertIn('画像分析尚未启用', report)

    def test_sampler_discards_idle_switch_sleep_and_clock_jump(self):
        sampler = Sampler(self.ledger)
        sampler.tick(100, 100, 'code.exe', False)
        sampler.tick(105, 105, 'code.exe', False)
        sampler.tick(110, 110, 'other.exe', False)
        sampler.tick(115, 115, 'other.exe', True)
        sampler.tick(120, 120, 'other.exe', False)
        sampler.tick(200, 200, 'other.exe', False)
        sampler.tick(220, 205, 'other.exe', False)
        self.assertEqual(self.ledger.totals(0, 300)['app'], {'code.exe': 5})

    def test_sampler_website_change_does_not_inherit_prior_domain(self):
        sampler = Sampler(self.ledger)
        sampler.tick(100, 100, 'chrome.exe', False, 'a.com')
        sampler.tick(105, 105, 'chrome.exe', False, 'a.com')
        sampler.tick(110, 110, 'chrome.exe', False, 'b.com')
        sampler.tick(115, 115, 'chrome.exe', False, 'b.com')
        sampler.tick(120, 120, 'chrome.exe', False)
        self.assertEqual(self.ledger.totals(0, 200),
                         {'app': {'chrome.exe': 20}, 'domain': {'a.com': 5, 'b.com': 5}})

    def test_sampler_clear_cannot_reintroduce_previous_interval(self):
        sampler = Sampler(self.ledger)
        sampler.tick(100, 100, 'code.exe', False)
        self.ledger.clear()
        sampler.tick(105, 105, 'code.exe', False)
        self.assertEqual(self.ledger.totals(0, 200)['app'], {})

    def test_invalid_inputs_do_not_enter_log(self):
        for value in ('https://example.com/private?q=x', '<system>', 'a..com', '-a.com', 'x\n.com'):
            with self.assertRaises(ValueError):
                domain_name(value)
        for end in (100, 200, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.ledger.append(100, end, 'code.exe')
        with self.assertRaises(ValueError):
            self.ledger.append(100, 105, 'C:/private/code.exe')

    def test_bridge_opt_in_browser_identity_expiry_and_exclusion(self):
        bridge = BrowserBridge('x' * 32, allowed=False)
        bridge.accept({'browser_app': 'chrome.exe', 'domain': 'example.com'}, 100)
        self.assertIsNone(bridge.current('chrome.exe', 100))
        bridge.allowed = True
        bridge.accept({'browser_app': 'chrome.exe', 'domain': 'example.com'}, 100)
        self.assertEqual(bridge.current('chrome.exe', 110), 'example.com')
        self.assertIsNone(bridge.current('msedge.exe', 110))
        self.assertIsNone(bridge.current('chrome.exe', 113))
        bridge.excluded = ('example.com',)
        bridge.accept({'browser_app': 'chrome.exe', 'domain': 'sub.example.com'}, 115)
        self.assertIsNone(bridge.current('chrome.exe', 115))


if __name__ == '__main__':
    unittest.main()
