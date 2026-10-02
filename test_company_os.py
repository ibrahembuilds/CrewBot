"""Company OS integration checks: actual curl, only an isolated localhost provider."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from contextlib import contextmanager
import copy
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import company_dashboard as company
from company_os import CompanyOS,OSHub
from providers import Vault,ProviderHTTP,PROVIDERS,complete
import scout

class ProviderFixture:
    def __init__(self):self.requests=[];self.calls=[];self.mode='normal'
    def __enter__(self):
        fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
                fixture.requests.append((self.path,body))
                if self.path=='/v1/chat/completions':
                    if fixture.mode=='http_error':
                        self.send_response(429);self.end_headers();self.wfile.write(b'{"error":{"message":"fixture quota"}}');return
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    self.wfile.write(b': fixture keepalive\n\n')
                    def emit(value):self.wfile.write(('data: '+json.dumps(value)+'\n\n').encode());self.wfile.flush()
                    if fixture.mode=='stream_error':emit({'error':{'message':'fixture generation error'}});return
                    if fixture.calls:
                        name,args=fixture.calls.pop(0);raw=json.dumps(args);cut=len(raw)//2
                        call_id='call_fixture_'+uuid.uuid4().hex
                        emit({'choices':[{'delta':{'tool_calls':[{'index':0,'id':call_id,'type':'function','function':{'name':name,'arguments':raw[:cut]}}]}}]})
                        emit({'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':raw[cut:]}}]},'finish_reason':'tool_calls'}]})
                    else:
                        emit({'choices':[{'delta':{'content':'Local fixture '}}]})
                        emit({'choices':[{'delta':{'content':'deliverable. No real company research.'},'finish_reason':None if fixture.mode=='incomplete' else 'stop'}]})
                    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush();return
                values={'/search':{'results':[{'title':'Fixture source','url':'https://example.com/fixture','content':'Local fixture research only.'}]},'/v2/scrape':{'success':True,'data':{'markdown':'# Fixture page','metadata':{'title':'Fixture'}}},'/alpha/decisions':{'answers':{'owner':{'choice':'growth','confidence':.9,'probabilities':{'growth':.9}}}},'/emails':{'id':'email_fixture'},'/auth.test':{'ok':True,'team':'Local fixture team'}}
                value=values.get(self.path,{'error':'unknown fixture path'})
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(value).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url=f'http://127.0.0.1:{self.server.server_port}'
        return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join(2)

class CompanyOSTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        for name in ('company.json','employees.json','agent.json'):shutil.copy((company.ROOT/'tests/fixtures'/name) if name in ('employees.json','company.json') else company.ROOT/name,self.folder/name)
        self.env=patch.dict(os.environ,{v[1]:'' for v in PROVIDERS.values()});self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def app(self):
        w=company.Workspace(self.folder);CompanyOS(w,False);return w
    def connect(self,w,fixture):
        for provider in PROVIDERS:w.os.vault.set(provider,'local-'+provider+'-fixture-key')
        w.os.http=ProviderHTTP(w.os.vault,fixture.url);w.operations.http=w.os.http
    def wait(self,w,t):
        with w.condition:self.assertTrue(w.condition.wait_for(lambda:t['id'] not in w.active,timeout=35))
    def test_tenant_isolation_onboarding_and_recommendation(self):
        hub=OSHub(company.Workspace(self.folder));new=hub.create({'name':'Fixture Bakery'})['company_id'];w=hub.get(new)
        w.os.onboard({'name':'Fixture Bakery','summary':'Bread and catering','market':'Local offices','goals':'Qualify catering leads','tools':'Slack'})
        self.assertEqual(list(w.roles),['mentor']);self.assertFalse(w.os.can_run())
        w.os.vault.set('openrouter','isolated-fixture-credential');self.assertFalse(hub.root.os.vault.get('openrouter'))
        with self.assertRaises(scout.ScoutError):w.os.bootstrap_team()
        proposal=[]
        for identifier,name,label,why in (
            ('director','Bread Planner','Operations coordinator','Coordinate reviewed catering priorities.'),
            ('growth','Bread Scout','Catering researcher','Find office catering leads with evidence.'),
            ('offers','Bread Offers','Catering proposal writer','Draft catering offers from approved research.'),
            ('success','Bread Care','Customer coordinator','Prepare client handoffs and reviewed updates.'),
        ):
            proposal.append({'id':identifier,'name':name,'role':label,'capability_role':identifier,'instructions':why+' Use the bakery’s saved context.','why':why})
        w.os.save_team_proposal({'roles':proposal,'connections':[{'name':'Slack','purpose':'Review proposed team updates before sending.'}]})
        roles=w.os.bootstrap_team()['employees'];self.assertEqual(len(roles),4)
        self.assertFalse(w.os.bootstrap_team()['employees']);self.assertNotIn('architect',w.roles)
        self.assertNotIn('Fixture Bakery',hub.root.snapshot()['company']['name'])
        with self.assertRaises(scout.ScoutError):w.os.openai_api()
        with self.assertRaises(scout.ScoutError):hub.get('../../outside')
        self.assertEqual(hub.snapshot(new)['token'],hub.root.token)
    def test_missing_key_chat_persists_owner_and_conversation(self):
        w=self.app();value=w.os.chat({'message':'Plan company priorities'});t=w.task(value['task_id'])
        self.assertEqual(t['employee_id'],'mentor');self.assertEqual(t['status'],'blocked')
        self.assertEqual(t['conversation_id'],value['conversation_id'])
        with self.assertRaises(scout.ScoutError):w.os.chat({'employee_id':'growth','message':'Wrong owner','conversation_id':value['conversation_id']})
        restarted=company.Workspace(self.folder);CompanyOS(restarted,False)
        self.assertEqual(len(restarted.store['conversations']),1)
    def test_streamed_mentor_delegates_and_specialist_saves_file(self):
        with ProviderFixture() as f:
            w=self.app();self.connect(w,f)
            f.calls=[('assign_work',{'employee_id':'growth','title':'Fixture research','description':'Local fixture only'})]
            value=w.os.chat({'message':'Give Scout a research task'});parent=w.task(value['task_id']);self.wait(w,parent)
            child=next(t for t in w.store['tasks'] if t['parent_id']==parent['id']);self.wait(w,child)
            self.assertEqual(parent['status'],'review',parent['error']);self.assertEqual(child['employee_id'],'growth')
            self.assertEqual(child['status'],'review',child['error']);self.assertIn('Local fixture',parent['result'])
            handler=w.os.handlers(child)['write_deliverable'];artifact=handler({'filename':'findings.md','content':'# Local fixture findings'})
            self.assertFalse(artifact['executed']);self.assertEqual(Path(child['artifacts'][0]['local_path']).read_text(),'# Local fixture findings')
            self.assertEqual(handler({'filename':'findings.md','content':'# Local fixture findings'})['artifact_id'],artifact['artifact_id'])
            self.assertTrue(any(message['role']=='tool' for path,body in f.requests if path=='/v1/chat/completions' for message in body['messages']))
    def test_blank_mentor_onboarding_stream_saves_profile_then_reviewable_team(self):
        scout.save_json(self.folder/'employees.json',[])
        original=scout.load_json(self.folder/'company.json')
        scout.save_json(self.folder/'company.json',{k:[] if isinstance(v,list) else '' for k,v in original.items()})
        profile={'name':'Fixture Dental','summary':'Dental treatment and prevention','market':'Local families','goals':'Reduce missed appointments','tools':'Email and calendar'}
        proposal={'roles':[{'id':'patient-coordinator','name':'Robin','role':'Patient coordinator','capability_role':'success','instructions':'Prepare appointment reminders for owner review using approved clinic information.','why':'Reduce missed appointments through reviewed reminders.'}],'connections':[{'name':'Calendar','purpose':'Read appointment dates after supported access is configured.'}]}
        with ProviderFixture() as f:
            w=self.app();self.connect(w,f)
            f.calls=[('update_business_profile',profile),('save_team_proposal',proposal)]
            value=w.os.chat({'message':'I run Fixture Dental, treating local families. Help reduce missed appointments with our email and calendar tools and recommend my team.'})
            task=w.task(value['task_id']);self.wait(w,task)
            self.assertEqual(task['status'],'review',task['error'])
            self.assertTrue(w.store['onboarding']['complete'])
            self.assertEqual(w.store['onboarding']['answers']['name'],'Fixture Dental')
            self.assertEqual(list(w.roles),['mentor'])
            self.assertEqual(w.store['team_proposal']['status'],'pending')
            requests=[body for path,body in f.requests if path=='/v1/chat/completions']
            self.assertEqual(len(requests),3)
            tool_messages=[message for message in requests[-1]['messages'] if message['role']=='tool']
            self.assertEqual(len(tool_messages),2)
            self.assertEqual(len({message['tool_call_id'] for message in tool_messages}),2)
            created=w.os.bootstrap_team()
            self.assertEqual(created['employees'][0]['employee_id'],'patient-coordinator')
            self.assertEqual(list(w.roles),['mentor','patient-coordinator'])
    def test_chat_role_change_has_scoped_tools_and_idempotency(self):
        w=self.app();t=w.create_task({'employee_id':'mentor','title':'Fixture role change','description':'Create an employee'},start=False)
        handler=w.os.handlers(t)['configure_role'];args={'id':'','name':'Fixture closer','role':'Offer specialist','instructions':'Draft scoped offers','capability_role':'offers'}
        first=handler(args);second=handler(args);self.assertEqual(first,second)
        identifier=first['employee_id'];config=scout.load_json(w.employee_folder(identifier)/'agent.json')
        names={x.get('name') for x in config['tools']};self.assertIn('save_offer',names);self.assertNotIn('assign_work',names)
        self.assertEqual(len([e for e in w.employees if e['id']==identifier]),1)
        w.os.upsert_role({'id':identifier,'name':'Fixture researcher','role':'Research','instructions':'Research sourced prospects','capability_role':'growth'})
        names={x.get('name') for x in scout.load_json(w.employee_folder(identifier)/'agent.json')['tools']}
        self.assertIn('save_leads',names);self.assertNotIn('save_offer',names)
    def test_memory_and_windows_protected_keys_are_redacted(self):
        v=Vault(self.folder,False);key='test-memory-key-unique';v.set('openrouter',key)
        self.assertFalse(v.path.exists());self.assertNotIn(key,scout.redact('Credential: '+key))
        with self.assertRaises(scout.ScoutError):scout.message_input('Use '+key)
        with self.assertRaises(scout.ScoutError):v.set('openrouter',key,'false')
        if os.name=='nt':
            v.set('openrouter',key,True);self.assertNotIn(key,v.path.read_text())
            self.assertEqual(Vault(self.folder,False).get('openrouter'),key)
            v.set('openrouter','replacement-session-key',False);self.assertFalse(Vault(self.folder,False).get('openrouter'))
        w=self.app();w.os.vault.set('tavily','private-fixture-tavily-key')
        self.assertNotIn('private-fixture-tavily-key',json.dumps(w.os.snapshot()))
    def test_stream_errors_and_incomplete_output_remain_blocked(self):
        for mode in ('http_error','stream_error','incomplete'):
            with self.subTest(mode=mode),ProviderFixture() as f:
                f.mode=mode;w=self.app();self.connect(w,f);t=w.create_task({'employee_id':'growth','title':mode,'description':'Local test'});self.wait(w,t)
                self.assertEqual(t['status'],'blocked');self.assertTrue(t['error'])
                if mode=='incomplete':self.assertIn('Local fixture',t['messages'][-1]['text'])
    def test_search_scraping_and_jev_use_actual_curl_routes(self):
        with ProviderFixture() as f:
            w=self.app();self.connect(w,f)
            self.assertEqual(w.os.search('Fixture')['sources'][0]['url'],'https://example.com/fixture')
            self.assertIn('Fixture page',w.os.scrape('https://example.com/fixture')['markdown'])
            self.assertEqual(w.os.route('Find leads')['employee_id'],'growth')
            self.assertEqual([r[0] for r in f.requests],['/search','/v2/scrape','/alpha/decisions'])
            for url in ('http://127.0.0.1/private','http://localhost','https://user:pass@example.com'):
                with self.assertRaises(scout.ScoutError):w.os.scrape(url)
    def test_schedule_once_until_review_and_no_key_no_dispatch(self):
        with ProviderFixture() as f:
            w=self.app();schedule=w.operations.save_schedule({'employee_id':'growth','title':'Daily fixture','description':'Local only','interval_hours':24,'enabled':True})
            w.operations.tick(time.time()+90000);self.assertFalse(w.store['tasks']);self.connect(w,f)
            clock=time.time()+90000;w.operations.tick(clock);t=w.task(w.store['schedules'][0]['last_task_id']);self.wait(w,t)
            self.assertEqual(t['status'],'review');w.operations.tick(clock+90000);self.assertEqual(len(w.store['tasks']),1)
            w.action({'task_id':t['id'],'action':'complete'});w.operations.tick(clock+90000);self.wait(w,w.store['tasks'][-1]);self.assertEqual(len(w.store['tasks']),2)
    def test_automatic_internal_handoff_and_daily_cap(self):
        with ProviderFixture() as f:
            w=self.app();self.connect(w,f);w.os.save_settings({'auto_handoffs':True,'daily_task_limit':1})
            s=w.operations.save_schedule({'employee_id':'offers','title':'Auto offer fixture','description':'Local only','interval_hours':24,'enabled':True})
            w.operations.tick(time.time()+90000);t=w.store['tasks'][0];self.wait(w,t);self.assertEqual(t['status'],'done')
            with self.assertRaises(scout.ScoutError):w.create_task({'employee_id':'growth','title':'Over cap','description':'Stop'},start=False)
    def test_report_email_receipt_and_uncertain_restart(self):
        with ProviderFixture() as f:
            w=self.app();self.connect(w,f);w.os.save_settings({'report_from':'reports@example.com','report_to':'owner@example.com'})
            report=w.os.report();w.os.send_report(report['id'])
            deadline=time.monotonic()+20
            while w.store['reports'][0]['delivery']=='sending' and time.monotonic()<deadline:time.sleep(.05)
            self.assertEqual(w.store['reports'][0]['delivery'],'sent');self.assertEqual(w.store['reports'][0]['receipt'],'email_fixture')
            with self.assertRaises(scout.ScoutError):w.os.send_report(report['id'])
            self.assertEqual(len([r for r in f.requests if r[0]=='/emails']),1)
            w.store['reports'][0]['delivery']='sending';w.changed();restarted=company.Workspace(self.folder);CompanyOS(restarted,False)
            self.assertEqual(restarted.store['reports'][0]['delivery'],'uncertain')
    def test_unsafe_settings_rejected(self):
        w=self.app()
        for settings in ({'chat_model':'typesafe/jev-1.13'},{'automatic_reports':True},{'daily_task_limit':0},{'provider':'unknown'},{'report_to':'bad'},{'auto_handoffs':'true'}):
            with self.subTest(settings=settings),self.assertRaises(scout.ScoutError):w.os.save_settings(settings)
    def test_http_company_actions_csrf_and_download_scope(self):
        w=company.Workspace(self.folder);hub=OSHub(w);server=ThreadingHTTPServer(('127.0.0.1',0),company.make_handler(w));server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(url+'/api/os/state?company=default') as response:self.assertIn('operating_system',json.load(response))
            req=Request(url+'/api/os/action',data=b'{"action":"report"}',headers={'Content-Type':'application/json'},method='POST')
            with self.assertRaises(HTTPError) as e:urlopen(req)
            self.assertEqual(e.exception.code,403)
            report=w.os.report();new=hub.create({'name':'Isolated company'})['company_id']
            with self.assertRaises(HTTPError) as e:urlopen(url+'/api/os/file?company='+new+'&report='+report['id'])
            self.assertEqual(e.exception.code,404)
            req=Request(url+'/api/os/action',data=b'{"action":"connection","data":{"connection":"slack","destinations":["C12345678"],"enabled":true}}',headers={'Content-Type':'application/json','X-Scout-Token':w.token},method='POST')
            with urlopen(req) as response:self.assertEqual(response.status,202)
            self.assertEqual(w.store['connections']['slack']['channels'],['C12345678'])
            with urlopen(url+'/api/os/events?company='+new,timeout=4) as response:
                self.assertEqual(response.headers['Content-Type'],'text/event-stream')
                lines=[response.readline().decode() for _ in range(3)]
                self.assertTrue(lines[0].startswith('id: '));self.assertIn('refresh',lines[1])
            with self.assertRaises(HTTPError):urlopen(url+'/bots/unknown.svg')
        finally:server.shutdown();server.server_close();thread.join(2)

if __name__=='__main__':unittest.main()
