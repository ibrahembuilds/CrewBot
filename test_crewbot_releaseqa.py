"""Release boundaries: blank templates, scoped branding and reusable employee flows."""
import base64
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import company_dashboard as dashboard
from company_os import OSHub
from providers import PROVIDERS
from providers import ProviderHTTP, complete
import scout
from test_crewbot_connections import LocalProvider, streamed_calls


class CrewBotReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.originals = {}
        for name in ('company.json', 'employees.json', 'agent.json'):
            source = dashboard.ROOT / name
            self.originals[name] = source.read_bytes()
            shutil.copy(source, self.folder / name)
        self.environment = patch.dict(os.environ, {**{v[1]: '' for v in PROVIDERS.values()}, 'OPENAI_PROJECT_ID': ''})
        self.environment.start()
        self.hub = OSHub(dashboard.Workspace(self.folder))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard.make_handler(self.hub.root))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.environment.stop()
        self.temporary.cleanup()
        for name, original in self.originals.items():
            self.assertEqual((dashboard.ROOT / name).read_bytes(), original, 'Tests must not change distributed templates.')

    def post(self, data, token=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['X-Scout-Token'] = token
        if origin:
            headers['Origin'] = origin
        return urlopen(Request(self.url + '/api/os/action', data=json.dumps(data).encode(), headers=headers, method='POST'))

    def test_distributed_templates_and_new_tenant_begin_blank(self):
        self.assertEqual(json.loads(self.originals['employees.json']), [])
        company = json.loads(self.originals['company.json'])
        self.assertFalse(any(company.values()))
        self.assertEqual(list(self.hub.root.roles), ['mentor'])
        self.assertFalse(self.hub.root.store['onboarding']['complete'])
        self.assertFalse(self.hub.root.store['tasks'])
        self.assertFalse(self.hub.root.store['projects'])
        self.assertFalse(self.hub.root.store['schedules'])
        new = self.hub.create({'name': 'Release fixture clinic'})['company_id']
        tenant = self.hub.get(new)
        self.assertEqual(list(tenant.roles), ['mentor'])
        self.assertFalse(tenant.os.can_run())
        self.assertEqual(tenant.os.settings['project_id'], '')
        self.assertFalse(tenant.store['onboarding']['complete'])

    def test_landing_dashboard_and_required_assets_are_served_separately(self):
        with urlopen(self.url + '/') as response:
            landing = response.read().decode()
            self.assertIn('<title>CrewBot', landing)
            self.assertIn('href="/app"', landing)
            self.assertIn('how-it-works', landing)
        with urlopen(self.url + '/app') as response:
            application = response.read().decode()
            self.assertIn('/os.js', application)
            self.assertNotEqual(application, landing)
        for asset in ('/landing.js', '/landing.css', '/os.js', '/os.css', '/brand/icon.svg', '/bots/mentor.svg'):
            with self.subTest(asset=asset), urlopen(self.url + asset) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 30)
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        for path in ('/company.json', '/.crewbot/default/company-state.json', '/credentials.dpapi.json', '/bots/../../company.json'):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urlopen(self.url + path)
            self.assertEqual(error.exception.code, 404)

    def test_branding_posts_require_root_csrf_and_logos_stay_in_company(self):
        new = self.hub.create({'name': 'Release fixture studio'})['company_id']
        tenant = self.hub.get(new)
        action = {'company_id': new, 'action': 'branding', 'data': {'accent': '#246abc', 'workspace_name': 'Studio Crew'}}
        for token, origin in ((None, None), (tenant.token, None), (self.hub.root.token, 'https://untrusted.example')):
            with self.subTest(token_kind='missing' if token is None else 'present', origin=origin), self.assertRaises(HTTPError) as error:
                self.post(action, token, origin)
            self.assertEqual(error.exception.code, 403)
        with self.post(action, self.hub.root.token, self.url) as response:
            self.assertEqual(response.status, 202)
        self.assertEqual(tenant.os.snapshot()['branding']['accent'], '#246abc')
        self.assertNotEqual(self.hub.root.os.snapshot()['branding']['accent'], '#246abc')
        raw = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        uri = 'data:image/png;base64,' + base64.b64encode(raw).decode()
        with self.post({'company_id': new, 'action': 'branding', 'data': {'logo_data': uri}}, self.hub.root.token):
            pass
        with urlopen(self.url + '/api/os/logo?company=' + new) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.read(), raw)
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + '/api/os/logo?company=default')
        self.assertEqual(error.exception.code, 404)
        with self.assertRaises(HTTPError):
            urlopen(self.url + '/api/os/logo?company=../../outside')

    def test_skill_templates_bind_to_custom_hires_and_block_missing_skills(self):
        app = self.hub.root.os
        templates = app.w.operations.templates()
        self.assertTrue(all(not item['available'] for item in templates))
        before = list(app.w.store['flows'])
        with self.assertRaises(scout.ScoutError):
            app.w.operations.create_flow({'template_id': 'prospecting', 'title': 'Unstaffed', 'brief': 'Fixture only'})
        self.assertEqual(app.w.store['flows'], before)
        app.update_business_profile({'name': 'Release studio', 'summary': 'Design services', 'market': 'Local businesses', 'goals': 'Find suitable clients'})
        app.save_team_proposal({'roles': [
            {'id': 'research-assistant', 'name': 'Robin', 'role': 'Lead researcher', 'capability_role': 'growth', 'instructions': 'Research suitable design clients with sources.', 'why': 'Find suitable design clients.'},
            {'id': 'proposal-writer', 'name': 'River', 'role': 'Proposal writer', 'capability_role': 'offers', 'instructions': 'Draft scoped design offers for review.', 'why': 'Turn approved research into scoped proposals.'},
        ], 'connections': []})
        app.bootstrap_team()
        template = next(item for item in app.w.operations.templates() if item['id'] == 'prospecting')
        self.assertTrue(template['available'])
        self.assertEqual([step['employee_id'] for step in template['steps']], ['research-assistant', 'proposal-writer'])
        flow = app.w.operations.create_flow({'template_id': 'prospecting', 'title': 'Scoped prospecting', 'brief': 'Fixture only'})
        self.assertEqual([step['employee_id'] for step in flow['steps']], ['research-assistant', 'proposal-writer'])
        self.assertEqual(flow['status'], 'ready')
        self.assertFalse(app.w.store['tasks'], 'Creating a reviewable flow must not start work.')

    def test_invalid_company_action_data_returns_error_without_partial_creation(self):
        before = list(self.hub.records)
        for data in (['invalid'], 'invalid', 42):
            with self.subTest(data_type=type(data).__name__), self.assertRaises(HTTPError) as error:
                self.post({'action': 'create_company', 'data': data}, self.hub.root.token)
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.hub.records, before)
        self.assertFalse((self.folder / 'companies').exists())

    def test_unexpected_terminal_stream_reason_cannot_execute_tools(self):
        self.hub.root.os.vault.set('openrouter', 'release-fixture-credential')
        tools_called = []
        tools = {'save_fixture': ({'name': 'save_fixture', 'parameters': {'type': 'object', 'properties': {}}}, lambda args: tools_called.append(args) or {'saved': True})}
        for reason in ('stop', 'unexpected_completion'):
            events = streamed_calls([('fixture_call', 'save_fixture', {'value': 'fixture'})], reason)
            with self.subTest(reason=reason), LocalProvider([(200, events)]) as fixture:
                http = ProviderHTTP(self.hub.root.os.vault, fixture.url)
                with self.assertRaises(scout.ScoutError):
                    complete(http, 'fixture/model', [{'role': 'user', 'content': 'Fixture only'}], tools, lambda text: None, lambda: False)
                self.assertFalse(tools_called)
                self.assertEqual(len(fixture.requests), 1)
        events = [{'choices': [{'delta': {'content': 'Fixture text'}, 'finish_reason': 'unknown'}]}]
        with LocalProvider([(200, events)]) as fixture:
            with self.assertRaises(scout.ScoutError):
                complete(ProviderHTTP(self.hub.root.os.vault, fixture.url), 'fixture/model', [{'role': 'user', 'content': 'Fixture only'}], {}, lambda text: None, lambda: False)


if __name__ == '__main__':
    unittest.main()
