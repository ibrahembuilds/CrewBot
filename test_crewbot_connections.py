"""Connection boundaries and tool execution checks using real curl to localhost only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import scout
from providers import PROVIDERS, ProviderHTTP, Vault, complete


class LocalProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                fixture.requests.append((self.path, body))
                status, payload = fixture.responses.pop(0)
                self.send_response(status)
                self.send_header('Content-Type', 'text/event-stream' if isinstance(payload, list) else 'application/json')
                self.end_headers()
                if isinstance(payload, list):
                    self.wfile.write(b': fixture heartbeat\n\n')
                    for event in payload:
                        self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                        self.wfile.flush()
                    self.wfile.write(b'data: [DONE]\n\n')
                elif isinstance(payload, bytes): self.wfile.write(payload)
                else: self.wfile.write(json.dumps(payload).encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        return self

    def __exit__(self, *args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)


def streamed_calls(calls, finish='tool_calls'):
    first = []
    second = []
    for index, (identifier, name, args) in enumerate(calls):
        raw = args if isinstance(args, str) else json.dumps(args)
        cut = len(raw) // 2
        first.append({'index': index, 'id': identifier, 'type': 'function', 'function': {'name': name[:3], 'arguments': raw[:cut]}})
        second.append({'index': index, 'function': {'name': name[3:], 'arguments': raw[cut:]}})
    return [{'choices': [{'delta': {'tool_calls': first}}]},
            {'choices': [{'delta': {'tool_calls': second}, 'finish_reason': finish}]}]


TEXT = [{'choices': [{'delta': {'content': 'Local fixture complete.'}, 'finish_reason': 'stop'}]}]


class CrewBotConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {v[1]: '' for v in PROVIDERS.values()})
        self.environment.start()
        self.vault = Vault(self.folder, False)
        self.vault.set('openrouter', 'fixture-openrouter-credential')

    def tearDown(self):
        self.environment.stop(); self.temporary.cleanup()

    def run_completion(self, fixture, calls):
        tools = {'save_fixture': ({'name': 'save_fixture', 'parameters': {'type': 'object', 'properties': {}}}, lambda args: calls.append(args) or {'saved': True})}
        return complete(ProviderHTTP(self.vault, fixture.url), 'fixture/model', [{'role': 'user', 'content': 'Local fixture only'}], tools, lambda text: None, lambda: False)

    def test_remove_suppresses_environment_key_after_restart_and_set_reenables(self):
        os.environ['OPENROUTER_API_KEY'] = 'fixture-environment-credential'
        vault = Vault(self.folder, True)
        self.assertEqual(vault.storage('openrouter'), 'environment')
        self.assertTrue(vault.get('openrouter'))
        result = vault.remove('openrouter')
        self.assertFalse(result['configured'])
        restarted = Vault(self.folder, True)
        self.assertEqual(restarted.storage('openrouter'), 'disabled')
        self.assertFalse(restarted.get('openrouter'))
        self.assertNotIn('fixture-environment-credential', restarted.path.read_text())
        restarted.set('openrouter', 'fixture-replacement-credential')
        self.assertEqual(restarted.storage('openrouter'), 'memory')
        self.assertTrue(restarted.get('openrouter'))

    def test_protected_storage_sources_and_partial_corruption(self):
        if os.name != 'nt': self.skipTest('Windows protected credential storage')
        self.vault.set('openrouter', 'fixture-protected-credential', True)
        self.vault.set('tavily', 'fixture-protected-tavily', True)
        self.assertEqual(self.vault.storage('openrouter'), 'protected')
        saved = scout.load_json(self.vault.path)
        saved['openrouter'] = 'corrupt-ciphertext'
        scout.save_json(self.vault.path, saved)
        restarted = Vault(self.folder, False)
        self.assertEqual(restarted.storage('openrouter'), 'disabled')
        self.assertEqual(restarted.storage('tavily'), 'protected')
        self.assertEqual(restarted.get('tavily'), 'fixture-protected-tavily')
        self.assertNotIn('fixture-protected-tavily', self.vault.path.read_text())

    def test_http_failure_redacts_credential_and_does_not_retry_mutation(self):
        key = self.vault.get('openrouter')
        with LocalProvider([(503, {'error': {'message': 'Echoed ' + key}})]) as fixture:
            with self.assertRaises(scout.ScoutError) as error:
                ProviderHTTP(self.vault, fixture.url).request('openrouter', 'POST', '/fixture-write', {'change': 'local'})
            self.assertNotIn(key, str(error.exception))
            self.assertIn('503', str(error.exception))
            self.assertEqual(len(fixture.requests), 1)
        with LocalProvider([(200, b'<html>not JSON</html>')]) as fixture:
            with self.assertRaisesRegex(scout.ScoutError, 'invalid JSON'):
                ProviderHTTP(self.vault, fixture.url).request('openrouter', 'POST', '/fixture', {})

    def test_fixed_origin_and_auth_header_cannot_be_overridden(self):
        for origin in ('https://example.com', 'http://localhost:1234', 'http://127.0.0.1:1234/path'):
            with self.subTest(origin=origin), self.assertRaises(scout.ScoutError): ProviderHTTP(self.vault, origin)
        http = ProviderHTTP(self.vault, 'http://127.0.0.1:1')
        for path, headers in (('//example.com', None), ('/safe\r\nInjected: true', None), ('/safe', ['Authorization: Bearer override']), ('/safe', ['X-Header: value\nInjected: true'])):
            with self.subTest(path=path, headers=headers), self.assertRaises(scout.ScoutError): http.request('openrouter', 'POST', path, {}, headers)

    def test_fragmented_calls_complete_and_reused_call_id_does_not_replay(self):
        batch = streamed_calls([('fixture_call', 'save_fixture', {'value': 'one'})])
        calls = []
        with LocalProvider([(200, batch), (200, batch), (200, TEXT)]) as fixture:
            self.assertEqual(self.run_completion(fixture, calls), 'Local fixture complete.')
            self.assertEqual(calls, [{'value': 'one'}])
            self.assertEqual(len(fixture.requests), 3)
            self.assertEqual(fixture.requests[1][1]['messages'][-1]['tool_call_id'], 'fixture_call')

    def test_entire_malformed_batch_is_blocked_before_first_mutation(self):
        batches = [
            [('same', 'save_fixture', {}), ('same', 'save_fixture', {})],
            [('good', 'save_fixture', {}), ('bad', 'save_fixture', '[]')],
            [('good', 'save_fixture', {}), ('bad', 'save_fixture', '{incomplete')],
            [('', 'save_fixture', {})],
        ]
        for batch in batches:
            calls = []
            with self.subTest(batch=batch), LocalProvider([(200, streamed_calls(batch))]) as fixture:
                with self.assertRaises(scout.ScoutError): self.run_completion(fixture, calls)
                self.assertFalse(calls)
                self.assertEqual(len(fixture.requests), 1)

    def test_truncated_tools_and_stream_error_have_no_execution_or_replay(self):
        for payload in (streamed_calls([('fixture_call', 'save_fixture', {})], 'length'), [{'error': {'message': 'fixture failure'}}]):
            calls = []
            with self.subTest(payload=payload), LocalProvider([(200, payload)]) as fixture:
                with self.assertRaises(scout.ScoutError): self.run_completion(fixture, calls)
                self.assertFalse(calls)
                self.assertEqual(len(fixture.requests), 1)


if __name__ == '__main__': unittest.main()
