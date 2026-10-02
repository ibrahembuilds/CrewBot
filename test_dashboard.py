"""Dashboard HTTP/security and agent integration tests."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import dashboard
import scout
from test_scout import Fixture


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        for filename in ('agent.json', 'initial-message.txt'):
            shutil.copy(scout.ROOT / filename, self.folder / filename)
        self.env = patch.dict(os.environ, {'OPENAI_API_KEY': ''})
        self.env.start()
        self.app = dashboard.Dashboard(self.folder)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.make_handler(self.app))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.env.stop()
        self.temporary.cleanup()

    def request(self, path, body=None, token=True, headers=None):
        supplied = {'Content-Type': 'application/json'}
        if token:
            supplied['X-Scout-Token'] = self.app.token
        supplied.update(headers or {})
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=supplied)
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_empty_state_and_no_secret_exposure(self):
        status, state = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertFalse(state['credential_ready'])
        self.assertEqual(state['project_id'], scout.PROJECT)
        self.assertEqual(state['messages'], [])
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'local-fixture-secret'}):
            self.assertNotIn('local-fixture-secret', json.dumps(self.request('/api/state')[1]))

    def test_csrf_host_and_origin_guards(self):
        for headers, token in [({}, False), ({'Origin': 'https://untrusted.example'}, True), ({'Host': 'untrusted.example'}, True)]:
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as caught:
                self.request('/api/brief', {'company': 'Example'}, token=token, headers=headers)
            self.assertEqual(caught.exception.code, 403)

    def test_brief_saves_without_api_key(self):
        self.request('/api/brief', {'company': 'Fixture company', 'region': 'SEA'})
        self.assertEqual(self.request('/api/state')[1]['brief']['company'], 'Fixture company')
        restored = dashboard.Dashboard(self.folder)
        self.assertEqual(restored.store['brief']['region'], 'SEA')

    def test_key_in_brief_is_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/brief', {'company': 'sk-proj-fictional-fixture-key'})
        self.assertEqual(caught.exception.code, 400)
        self.assertFalse(self.app.store_path.exists())

    def test_live_action_without_key_reports_setup(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/action/start', {})
        body = json.loads(caught.exception.read())
        self.assertIn('OPENAI_API_KEY', body['error'])
        self.assertFalse(self.app.busy)

    def test_local_agent_settings_without_key(self):
        agent = scout.load_json(self.folder / 'agent.json')
        body = {'name': 'My Scout', 'model': agent['model'], 'instructions': agent['instructions'], 'effort': 'high', 'verbosity': 'low'}
        thread = self.app.launch('settings', body)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        saved = scout.load_json(self.folder / 'agent.json')
        self.assertEqual(saved['name'], 'My Scout')
        self.assertEqual(saved['reasoning']['effort'], 'high')

    def test_real_curl_job_populates_messages_leads_and_export(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'local-fixture-credential'}), Fixture() as fixture:
            self.app.api_factory = lambda: scout.CurlAPI(base_url=fixture.url, timeout=10)
            job = self.app.launch('start')
            job.join(timeout=15)
            self.assertFalse(job.is_alive())
            self.assertIsNone(self.app.error)
            self.assertEqual(len(self.app.store['leads']), 5)
            self.assertEqual(self.app.store['messages'][-1]['session_id'], 'sess_fixture')
            self.assertFalse(self.app.store['messages'][-1]['streaming'])
            self.assertTrue(any(e['kind'] == 'text' for e in self.app.events))
            status, exported = self.request('/api/export')
            self.assertEqual(status, 200)
            self.assertEqual(list(exported[0]), ['Lead Name', 'Type of Lead', 'Reasoning and Evaluation', 'Conclusion & Recommendation'])
            followup = self.app.launch('send', {'message': 'Prioritize collaborators'})
            followup.join(timeout=15)
            self.assertIsNone(self.app.error)
            self.assertEqual(self.app.state_file()['turn_id'], 'turn_second')

    def test_parallel_mutations_blocked(self):
        self.app.busy = True
        with self.assertRaisesRegex(scout.ScoutError, 'working'):
            self.app.launch('start')

    def test_static_assets_and_unknown_paths(self):
        for path, content_type in [('/', 'text/html'), ('/app.js', 'text/javascript'), ('/styles.css', 'text/css')]:
            with urlopen(self.url + path, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers['Content-Type'].startswith(content_type))
                self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.url + '/../agent.json')
        self.assertEqual(caught.exception.code, 404)


if __name__ == '__main__':
    unittest.main(verbosity=2)
