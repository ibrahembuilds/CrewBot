"""Employee workflow checks with real curl against an isolated local API."""
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import company_dashboard as company
import scout
from test_scout import Fixture

class CompanyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        for name in ('employees.json','company.json','agent.json'):
            shutil.copy((company.ROOT/'tests/fixtures'/name) if name in ('employees.json','company.json') else company.ROOT/name,self.folder/name)
        self.env = patch.dict(os.environ, {'OPENAI_API_KEY':'local-fixture-credential'})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def wait(self,app,task):
        with app.condition:
            self.assertTrue(app.condition.wait_for(lambda:task['id'] not in app.active,timeout=45),app.snapshot()['tasks'])
    def test_no_key_queue_and_persistence(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}):
            app = company.Workspace(self.folder)
            t = app.create_task({'title':'Discovery','description':'Create a questionnaire','employee_id':'success'})
            self.assertEqual(t['status'],'blocked')
            self.assertFalse(app.snapshot()['credential_ready'])
            restarted = company.Workspace(self.folder)
            self.assertEqual(restarted.task(t['id'])['description'],'Create a questionnaire')
            self.assertEqual(len(restarted.snapshot()['employees']),len(scout.load_json(company.ROOT/'tests/fixtures/employees.json')))
    def test_curl_session_result_artifact_and_review(self):
        with Fixture() as f:
            app = company.Workspace(self.folder,lambda:scout.CurlAPI(base_url=f.url,timeout=15))
            t = app.create_task({'title':'Research fixture','description':'Local test only','employee_id':'growth'})
            self.wait(app,t)
            self.assertEqual(t['status'],'review',t.get('error'))
            self.assertIn('Fixture lead',t['result'])
            self.assertEqual(Path(t['artifacts'][0]['local_path']).read_bytes(),b'Fixture artifact; local test only.\n')
            create = next(r for r in f.requests if r[1] == '/v1/agents/sessions')
            self.assertEqual(create[2]['agent_id'],'agent_fixture')
            self.assertIn('FixtureCo',json.dumps(create[2]['input']))
            self.assertEqual(create[2]['environment']['type'],'openai_hosted')
            app.action({'task_id':t['id'],'action':'complete'})
            self.assertEqual(t['status'],'done')
    def test_director_delegation_roundtrip_and_limit(self):
        with Fixture('tool') as director, Fixture() as specialist:
            director.tool_name = 'delegate_task'
            director.tool_arguments = {'employee_id':'architect','title':'Architecture fixture','instructions':'Local fixture architecture only'}
            counter = iter([director,specialist])
            app = company.Workspace(self.folder,lambda:scout.CurlAPI(base_url=next(counter).url,timeout=60))
            t = app.create_task({'employee_id':'director','title':'Director fixture','description':'Delegate a local test'})
            self.wait(app,t)
            self.assertEqual(t['status'],'review',t['error'])
            child = next(c for c in app.store['tasks'] if c['parent_id'] == t['id'])
            self.assertEqual(child['status'],'review',child['error'])
            result = next(r for r in director.requests if r[0] == 'POST' and r[1].endswith('/events'))[2]['events'][0]
            self.assertTrue(result['success'])
            self.assertEqual(json.loads(result['output'])['task_id'],child['id'])
            t.update(status='running',delegations=8)
            with self.assertRaises(scout.ScoutError):
                app.delegate(t,{'employee_id':'growth','title':'Too many','instructions':'Stop'})
            with self.assertRaises(scout.ScoutError):
                app.delegate(t,{'employee_id':'director','title':'Loop','instructions':'Stop'})
    def test_company_updates_and_role_configuration(self):
        app = company.Workspace(self.folder)
        app.company({'market':'UAE and Saudi Arabia; healthcare SMEs'})
        self.assertIn('healthcare',app.snapshot()['company']['market'])
        roles = app.snapshot()['employees']
        self.assertEqual(roles[1]['configuration']['name'],'Business Lead and Partnership Scout')
        self.assertIn('delegate_task',[x.get('name') for x in roles[0]['configuration']['tools']])
        self.assertNotIn('delegate_task',[x.get('name') for x in roles[1]['configuration']['tools']])
        with self.assertRaises(scout.ScoutError):
            app.create_task({'employee_id':'unknown','title':'Bad','description':'Bad'})
        with self.assertRaises(scout.ScoutError):
            app.company({'summary':'sk-example-credential-long'})
    def test_http_csrf_and_download_paths(self):
        app = company.Workspace(self.folder)
        server = ThreadingHTTPServer(('127.0.0.1',0),company.make_handler(app))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url+'/api/state') as response:
                self.assertEqual(len(json.load(response)['employees']),len(scout.load_json(company.ROOT/'tests/fixtures/employees.json')))
            req = Request(url+'/api/tasks',data=json.dumps({'title':'Bad','description':'CSRF'}).encode(),headers={'Content-Type':'application/json'},method='POST')
            with self.assertRaises(HTTPError) as error:
                urlopen(req)
            self.assertEqual(error.exception.code,403)
            with self.assertRaises(HTTPError) as error:
                urlopen(url+'/api/artifact?task=../../company.json&id=foo')
            self.assertEqual(error.exception.code,404)
            request = Request(url+'/api/shutdown',data=b'{}',headers={'Content-Type':'application/json','X-Scout-Token':app.token},method='POST')
            app.active.add('restart-protection')
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code,400)
            app.active.clear()
            with urlopen(request) as response:
                self.assertEqual(response.status,202)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)
if __name__ == '__main__':
    unittest.main()
