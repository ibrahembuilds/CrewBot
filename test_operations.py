"""Review gates, recovery, scheduling and real curl provider transport checks."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import company_dashboard as company
from operations import ConnectionHTTP
import scout

class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
        for name in ('employees.json','company.json','agent.json'):
            shutil.copy((company.ROOT/'tests/fixtures'/name) if name in ('employees.json','company.json') else company.ROOT/name,self.folder/name)
        self.env=patch.dict(os.environ,{'OPENAI_API_KEY':'','GITHUB_TOKEN':'fixture-github-token','SLACK_BOT_TOKEN':'fixture-slack-token'})
        self.env.start()
        self.app=company.Workspace(self.folder)
        self.ops=self.app.operations
        self.launched=[]
        self.app.launch=lambda identifier,resume=False:self.launched.append(identifier)
    def tearDown(self):
        self.env.stop();self.temp.cleanup()
    def task(self,role):
        return self.app.create_task({'employee_id':role,'title':'Fixture task','description':'Local test only'},start=False)
    def test_seven_stage_review_gates_and_persisted_handoffs(self):
        flow=self.ops.create_flow({'template_id':'lead-to-delivery','title':'Fixture workflow','brief':'Build a fixture CRM'})
        self.assertEqual(self.launched,[])
        self.ops.start_flow(flow['id'])
        record=self.ops.find('flows',flow['id'])
        for i,stage in enumerate(record['steps']):
            task=self.app.task(stage['task_id'])
            task.update(status='review',result='Reviewed output '+str(i))
            self.ops.sync_flow(task)
            self.assertEqual(len(self.app.store['tasks']),i+1)
            self.ops.flow_action({'id':flow['id'],'action':'approve'})
            if i<6:
                next_task=self.app.task(record['steps'][i+1]['task_id'])
                self.assertIn('Reviewed output '+str(i),next_task['description'])
        self.assertEqual(record['status'],'completed')
        self.assertEqual(len(self.launched),7)
        with self.assertRaises(scout.ScoutError):self.ops.flow_action({'id':flow['id'],'action':'approve'})
        restarted=company.Workspace(self.folder)
        self.assertEqual(restarted.store['flows'][0]['status'],'completed')
        self.assertEqual(len(restarted.store['tasks']),7)
    def test_paused_flow_keeps_approved_stage_until_resume(self):
        flow=self.ops.create_flow({'template_id':'prospecting','title':'Fixture plan','brief':'Research fixture accounts'})
        self.ops.start_flow(flow['id'])
        record=self.ops.find('flows',flow['id'])
        task=self.app.task(record['steps'][0]['task_id'])
        with self.assertRaises(scout.ScoutError):self.ops.flow_action({'id':flow['id'],'action':'approve'})
        self.ops.flow_action({'id':flow['id'],'action':'pause'})
        task.update(status='review',result='Fixture evidence')
        self.ops.flow_action({'id':flow['id'],'action':'approve'})
        self.assertEqual(len(self.app.store['tasks']),1)
        self.ops.start_flow(flow['id'])
        self.assertEqual(len(self.app.store['tasks']),2)
    def test_role_permissions_and_replayed_local_tool_deduplication(self):
        growth=self.ops.handlers(self.task('growth'))
        self.assertNotIn('save_offer',growth)
        self.assertNotIn('propose_external_action',growth)
        self.assertNotIn('github_list_issues',growth)
        handler=self.ops.handlers(self.task('offers'))['save_offer']
        args={'title':'Fixture offer','client':'Fixture client','content':'Estimate only; local fixture.'}
        self.assertEqual(handler(args)['id'],handler(args)['id'])
        self.assertEqual(len(self.app.store['offers']),1)
    def test_leads_require_sources_and_pages_prevent_stale_edits(self):
        lead={'name':'Fixture account','type':'client','evaluation':'Source-backed fixture evaluation','recommendation':'Discover needs','sources':[]}
        with self.assertRaises(scout.ScoutError):self.ops.save_leads({'leads':[lead]})
        lead['sources']=['https://example.com/fixture']
        self.ops.save_leads({'leads':[lead]});self.ops.save_leads({'leads':[lead]})
        self.assertEqual(len(self.app.store['leads']),1)
        page=self.ops.save_page({'title':'Fixture note','content':'First draft','kind':'note'})
        args={**page,'content':'Updated draft'}
        self.ops.save_page(args)
        with self.assertRaises(scout.ScoutError):self.ops.save_page(args)
    def test_milestone_assignment_is_unique_and_completion_requires_review(self):
        project=self.ops.create_project({'title':'Fixture CRM','client':'Fixture client','brief':'Build a small fixture'})
        milestone=self.ops.milestone({'project_id':project['id'],'title':'Prototype','employee_id':'product','acceptance':'Setup instructions and passing checks'})
        body={'project_id':project['id'],'id':milestone['id'],'action':'assign'}
        first=self.ops.milestone_action(body);second=self.ops.milestone_action(body)
        self.assertEqual(first['id'],second['id']);self.assertEqual(len(self.launched),1)
        task=self.app.task(first['id']);task['status']='review';self.ops.sync_flow(task)
        saved=self.ops.find('projects',project['id'])
        self.assertEqual(saved['milestones'][0]['status'],'review')
        self.app.action({'task_id':task['id'],'action':'complete'})
        self.assertEqual(saved['milestones'][0]['status'],'completed')
        self.assertEqual(saved['status'],'planned')
    def test_responsibility_waits_for_key_and_unreviewed_previous_run(self):
        schedule=self.ops.save_schedule({'employee_id':'growth','title':'Fixture monitoring','description':'Research fixture changes','interval_hours':24,'enabled':True})
        clock=schedule['next_run']+1
        self.ops.tick(clock);self.assertEqual(self.launched,[])
        with patch.dict(os.environ,{'OPENAI_API_KEY':'fixture-openai-token'}):
            self.ops.tick(clock);self.assertEqual(len(self.launched),1)
            self.ops.tick(clock+90000);self.assertEqual(len(self.launched),1)
            self.app.task(self.launched[0])['status']='done'
            self.ops.tick(clock+90000);self.assertEqual(len(self.launched),2)
    def configure(self):
        self.ops.configure_connection({'connection':'slack','enabled':True,'destinations':['C12345678']})
        self.ops.configure_connection({'connection':'github','enabled':True,'destinations':['fixture/workspace']})
    def proposal(self,key='slack',content='Fixture review only'):
        return self.ops.propose({'connection':key,'destination':'C12345678' if key=='slack' else 'fixture/workspace','title':'Fixture issue','content':content,'reason':'Fixture action test'})
    def test_allowlists_and_exact_once_human_approved_dispatch(self):
        self.configure();requests=[]
        class HTTP:
            def request(inner,*args):
                requests.append(args);return {'ok':True,'channel':'C12345678','ts':'123.45'}
        self.ops.http=HTTP()
        with self.assertRaises(scout.ScoutError):self.ops.slack_history('C99999999')
        item=self.proposal();self.assertEqual(requests,[])
        self.ops.approval_action({'id':item['approval_id'],'action':'approve'})
        with self.app.condition:
            self.assertTrue(self.app.condition.wait_for(lambda:self.ops.find('approvals',item['approval_id'])['status']=='sent',timeout=5))
        self.assertEqual(requests[0][3]['text'],'Fixture review only')
        with self.assertRaises(scout.ScoutError):self.ops.approval_action({'id':item['approval_id'],'action':'approve'})
        self.assertEqual(len(requests),1)
        declined=self.proposal(content='Different fixture action')
        self.ops.approval_action({'id':declined['approval_id'],'action':'decline'})
        self.assertEqual(len(requests),1)
    def test_uncertain_external_writes_never_replay_after_restart(self):
        self.configure()
        class HTTP:
            def request(inner,*args):raise scout.ScoutError('Lost receipt with fixture-slack-token')
        self.ops.http=HTTP();item=self.proposal()
        self.ops.approval_action({'id':item['approval_id'],'action':'approve'})
        with self.app.condition:
            self.assertTrue(self.app.condition.wait_for(lambda:self.ops.find('approvals',item['approval_id'])['status']=='uncertain',timeout=5))
        self.assertNotIn('fixture-slack-token',self.app.path.read_text())
        approval=self.ops.find('approvals',item['approval_id']);approval['status']='executing';self.app.changed()
        restarted=company.Workspace(self.folder)
        self.assertEqual(restarted.store['approvals'][0]['status'],'uncertain')
    def test_connection_curl_transports_scope_read_and_reviewed_issue(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):self.reply()
            def do_POST(self):self.reply()
            def reply(self):
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
                requests.append((self.command,self.path,body,self.headers.get('Authorization')))
                if self.path=='/auth.test':value={'ok':True,'team':'Fixture team','user_id':'U123'}
                elif self.path.startswith('/conversations.history'):value={'ok':True,'messages':[{'text':'Fixture message','ts':'1'}],'has_more':False}
                elif self.path.endswith('/issues') and self.command=='POST':value={'number':7,'html_url':'https://github.com/fixture/workspace/issues/7'}
                elif '/issues?' in self.path:value=[{'number':2,'title':'Fixture issue','body':'Fixture body','html_url':'https://example.com/2'}]
                else:value={'full_name':'fixture/workspace'}
                data=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',len(data));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.ops.http=ConnectionHTTP(f'http://127.0.0.1:{server.server_port}')
            self.configure();self.ops.check_connection('slack');self.ops.check_connection('github')
            self.assertEqual(self.ops.slack_history('C12345678')['messages'][0]['text'],'Fixture message')
            self.assertEqual(self.ops.github_issues('fixture/workspace')[0]['number'],2)
            item=self.proposal('github');self.ops.approval_action({'id':item['approval_id'],'action':'approve'})
            with self.app.condition:
                self.assertTrue(self.app.condition.wait_for(lambda:self.ops.find('approvals',item['approval_id'])['status']=='sent',timeout=10))
            post=next(r for r in requests if r[1].endswith('/issues') and r[0]=='POST')
            self.assertEqual(post[2],{'title':'Fixture issue','body':'Fixture review only'})
            self.assertEqual(post[3],'Bearer fixture-github-token')
        finally:server.shutdown();server.server_close();thread.join(2)
    def test_all_credentials_are_rejected_from_messages_and_redacted(self):
        for value in ('ghp_testcredentiallong','github_pat_testcredentiallong','xoxb-testcredentiallong','sk-testcredentiallong'):
            self.assertEqual(scout.redact(value),'[REDACTED]')
            with self.assertRaises(scout.ScoutError):scout.message_input(value)

if __name__=='__main__':unittest.main()
