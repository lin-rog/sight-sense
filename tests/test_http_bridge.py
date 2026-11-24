import http.client
import json
import threading
import time
import unittest

from collector import BrowserBridge, bridge_server


class BridgeHttpTest(unittest.TestCase):
    def setUp(self):
        self.bridge = BrowserBridge('x' * 32)
        self.server = bridge_server(self.bridge, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def post(self, *, token='x' * 32, origin='chrome-extension://' + 'a' * 32, domain='example.com'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request('POST', '/v1/tab', json.dumps({'browser_app': 'chrome.exe', 'domain': domain}),
                               {'Origin': origin, 'X-Sight-Token': token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_token_and_extension_origin_are_both_required(self):
        self.assertEqual(self.post(token='wrong'), 403)
        self.assertEqual(self.post(origin='https://example.com'), 403)
        self.assertIsNone(self.bridge.current('chrome.exe', time.monotonic()))
        self.assertEqual(self.post(), 204)
        self.assertEqual(self.bridge.current('chrome.exe', time.monotonic()), 'example.com')

    def test_url_rejected_and_null_clears_domain(self):
        self.assertEqual(self.post(domain='https://example.com/private?token=secret'), 400)
        self.assertEqual(self.post(), 204)
        self.assertEqual(self.post(domain=None), 204)
        self.assertIsNone(self.bridge.current('chrome.exe', time.monotonic()))


if __name__ == '__main__':
    unittest.main()
