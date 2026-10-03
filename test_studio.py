"""Studio checks: OpenRouter Image/Video APIs, budgets, expert tools and sandboxed pages via actual curl and a localhost fixture."""
import base64
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import company_dashboard as company
from company_os import CompanyOS,OSHub,EXPERTS
from media import month_key
from providers import ProviderHTTP,PROVIDERS
import scout

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
SVG=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script><rect width="10" height="10"/></svg>'
MP4=b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom'+b'\x00'*256

class OpenRouterFixture:
    """Mirrors the documented OpenRouter response shapes for images, videos, catalogs and streamed chat."""
    def __init__(self):
        self.requests=[];self.responder=None;self.image=PNG;self.image_cost=0.04;self.video_states=['in_progress','completed'];self.video_cost=0.4;self.calls=[];self.chat_cost=0.0123;self.content_status=200
    def __enter__(self):
        fixture=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def send(self,value,status=200,mime='application/json'):
                raw=value if isinstance(value,bytes) else json.dumps(value).encode()
                self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                fixture.requests.append(('GET',self.path,None,self.headers.get('Authorization')))
                if self.path=='/v1/images/models':return self.send({'data':[{'id':'google/gemini-3.1-flash-image','name':'Gemini Image','architecture':{'input_modalities':['text','image'],'output_modalities':['image']},'supported_parameters':{'aspect_ratio':{'type':'enum','values':['1:1','16:9']},'n':{'type':'boolean'}}},{'id':'recraft/recraft-v4.1-vector','name':'Recraft vector','supported_parameters':{'output_format':{'type':'enum','values':['svg','png']}}},{'id':'../evil','name':'bad'}]})
                if self.path=='/v1/videos/models':return self.send({'data':[{'id':'google/veo-3.1-lite','name':'Veo lite','supported_durations':[4,6,8],'supported_resolutions':['720p'],'supported_aspect_ratios':['16:9','9:16'],'pricing_skus':{'duration_seconds_without_audio':'0.05'}}]})
                if self.path=='/v1/models':return self.send({'data':[{'id':'anthropic/claude-sonnet-5.5','name':'Sonnet','supported_parameters':['tools'],'pricing':{'prompt':'0.000002','completion':'0.00001'},'context_length':1000000},{'id':'some/no-tools','supported_parameters':['temperature']}]})
                if self.path.startswith('/v1/videos/job_') and self.path.endswith('/content?index=0'):
                    if fixture.content_status!=200:return self.send({'error':'gone'},fixture.content_status)
                    return self.send(MP4,mime='video/mp4')
                if self.path.startswith('/v1/videos/job_'):
                    status=fixture.video_states.pop(0) if fixture.video_states else 'completed'
                    value={'id':self.path.rsplit('/',1)[1],'status':status}
                    if status=='completed':value.update(unsigned_urls=[f'{fixture.url}{self.path}/content?index=0'],usage={'cost':fixture.video_cost,'is_byok':False})
                    if status=='failed':value['error']='Content policy violation'
                    return self.send(value)
                return self.send({'error':'unknown fixture path'},404)
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
                fixture.requests.append(('POST',self.path,body,self.headers.get('Authorization')))
                if self.path=='/v1/images':
                    count=body.get('n',1)
                    return self.send({'created':1748372400,'data':[{'b64_json':base64.b64encode(fixture.image).decode(),'media_type':'image/png'} for _ in range(count)],'usage':{'total_tokens':4175,'cost':fixture.image_cost}})
                if self.path=='/v1/videos':return self.send({'id':'job_'+uuid.uuid4().hex[:8],'polling_url':'unused','status':'pending'},202)
                if self.path=='/v1/chat/completions':
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                    def emit(value):self.wfile.write(('data: '+json.dumps(value)+'\n\n').encode());self.wfile.flush()
                    if fixture.responder and not fixture.calls:fixture.calls.extend(fixture.responder(body))
                    if fixture.calls:
                        name,args=fixture.calls.pop(0)
                        emit({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_'+uuid.uuid4().hex,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}]})
                    else:
                        emit({'choices':[{'delta':{'content':'Fixture deliverable.'},'finish_reason':'stop'}]})
                    emit({'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':5,'cost':fixture.chat_cost}})
                    self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush();return
                return self.send({'error':'unknown fixture path'},404)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url=f'http://127.0.0.1:{self.server.server_port}'
        return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def posted(self,path):return [r[2] for r in self.requests if r[0]=='POST' and r[1]==path]

class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        shutil.copy(company.ROOT/'tests/fixtures/company.json',self.folder/'company.json')
        (self.folder/'employees.json').write_text('[]',encoding='utf-8');shutil.copy(company.ROOT/'agent.json',self.folder/'agent.json')
        self.env=patch.dict(os.environ,{v[1]:'' for v in PROVIDERS.values()});self.env.start()
    def tearDown(self):self.env.stop();self.temp.cleanup()
    def app(self,fixture=None):
        w=company.Workspace(self.folder);CompanyOS(w,False)
        if fixture:
            w.os.vault.set('openrouter','local-openrouter-fixture-key');w.os.http=ProviderHTTP(w.os.vault,fixture.url);w.operations.http=w.os.http
        return w
    def wait(self,w,t):
        with w.condition:self.assertTrue(w.condition.wait_for(lambda:t['id'] not in w.active,timeout=35))
    def wait_job(self,w,job_id,states=('completed','failed')):
        deadline=time.time()+20
        while time.time()<deadline:
            job=next(j for j in w.store['media_jobs'] if j['id']==job_id)
            if job['status'] in states and job_id not in w.os.media.polling:return job
            time.sleep(.05)
        self.fail('Video job did not settle: '+json.dumps(job))

    def test_image_generation_saves_brand_aware_asset_and_real_cost(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture);w.os.save_branding({'visual_style':'Minimal flat geometric','brand_voice':'Warm and direct'})
            result=w.os.media.generate_image({'prompt':'A fox mark for a bakery','purpose':'logo','n':2})
            request=fixture.posted('/v1/images')[0]
            self.assertEqual(request['aspect_ratio'],'1:1');self.assertEqual(request['n'],2);self.assertEqual(request['model'],'google/gemini-3.1-flash-image')
            self.assertIn('Minimal flat geometric',request['prompt']);self.assertIn('Brand guidelines',request['prompt'])
            self.assertEqual(fixture.requests[0][3],'Bearer local-openrouter-fixture-key')
            self.assertEqual(len(result['assets']),2);self.assertEqual(result['cost'],0.04)
            for item in result['assets']:
                stored=w.os.media.asset(item['id'])
                self.assertEqual(w.os.media.asset_path(stored).read_bytes(),PNG);self.assertEqual(stored['mime'],'image/png');self.assertAlmostEqual(stored['cost'],0.02)
                self.assertNotIn('filename',item)
            spend=w.os.media.spend();self.assertAlmostEqual(spend['by_kind']['image'],0.04);self.assertEqual(spend['month'],month_key())
            self.assertNotIn(base64.b64encode(PNG).decode(),(self.folder/'company-state.json').read_text(),'Image bytes must not bloat persisted state')

    def test_budget_blocks_generation_before_any_provider_call(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture)
            with w.lock:w.store['usage'].append({'time':'x','month':month_key(),'kind':'image','model':'m','cost':10.0,'task_id':None,'employee_id':None,'asset_ids':[]})
            with self.assertRaisesRegex(scout.ScoutError,'Monthly AI budget'):w.os.media.generate_image({'prompt':'Poster','purpose':'poster'})
            with self.assertRaisesRegex(scout.ScoutError,'Monthly AI budget'):w.os.media.start_video({'prompt':'Clip'})
            self.assertEqual(fixture.posted('/v1/images'),[]);self.assertEqual(fixture.posted('/v1/videos'),[])
            # Last month's spend does not count toward this month.
            with w.lock:w.store['usage'][-1]['month']='2000-01'
            w.os.media.generate_image({'prompt':'Poster','purpose':'poster'})
            self.assertEqual(fixture.posted('/v1/images')[0]['aspect_ratio'],'2:3')

    def test_rejects_invalid_image_payloads_and_requests(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture)
            for bad in ({'prompt':'x','purpose':'nope'},{'prompt':'x','n':9},{'prompt':'x','model':'../../etc'},{'prompt':'x','aspect_ratio':'7:3'},{'prompt':''}):
                with self.assertRaises(scout.ScoutError):w.os.media.generate_image(bad)
            fixture.image=b'not an image at all'
            with self.assertRaisesRegex(scout.ScoutError,'unrecognized image'):w.os.media.generate_image({'prompt':'A banner','purpose':'banner'})
            self.assertEqual(w.store['assets'],[])
            self.assertAlmostEqual(w.os.media.spend()['by_kind']['image'],0.04,msg='A billed 2xx response counts even if its payload is unusable')
            fixture.image=PNG;fixture.image_cost=float('nan');before=w.os.media.spend()['total']
            w.os.media.generate_image({'prompt':'A banner','purpose':'banner'})
            self.assertEqual(w.os.media.spend()['total'],before,'Non-finite costs are ignored, never stored')
            json.dumps(w.os.snapshot(),allow_nan=False)

    def test_video_lifecycle_poll_download_and_cost(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture)
            started=w.os.media.start_video({'prompt':'Slow push-in on a storefront','duration':4,'aspect_ratio':'9:16','generate_audio':False})
            job=started['job'];self.assertEqual(job['status'],'pending')
            sent=fixture.posted('/v1/videos')[0];self.assertEqual(sent['duration'],4);self.assertEqual(sent['aspect_ratio'],'9:16');self.assertFalse(sent['generate_audio']);self.assertEqual(sent['model'],'google/veo-3.1-lite')
            w.os.media.poll_videos(clock=time.time()+60);self.assertEqual(self.wait_job(w,job['id'],('in_progress',))['status'],'in_progress')
            w.os.media.poll_videos(clock=time.time()+60);done=self.wait_job(w,job['id'])
            self.assertEqual(done['status'],'completed',done);self.assertAlmostEqual(done['cost'],0.4)
            asset=w.os.media.asset(done['asset_id']);self.assertEqual(asset['mime'],'video/mp4');self.assertEqual(w.os.media.asset_path(asset).read_bytes(),MP4)
            self.assertAlmostEqual(w.os.media.spend()['by_kind']['video'],0.4)
            self.assertFalse(list((self.folder/'assets').glob('*.part'))+list((self.folder/'assets').glob('download-*')))

    def test_video_failures_are_reported_and_retries_are_bounded(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture)
            fixture.video_states=['failed']
            job=w.os.media.start_video({'prompt':'Rejected clip'})['job']
            w.os.media.poll_videos(clock=time.time()+60);failed=self.wait_job(w,job['id'])
            self.assertEqual(failed['status'],'failed');self.assertIn('Content policy',failed['error']);self.assertEqual(w.store['usage'],[])
            fixture.video_states=[];fixture.content_status=500
            job=w.os.media.start_video({'prompt':'Download keeps failing'})['job']
            for attempt in range(5):
                w.os.media.poll_videos(clock=time.time()+10_000);settled=self.wait_job(w,job['id'],('in_progress','failed'))
            self.assertEqual(settled['status'],'failed');self.assertEqual(settled['failures'],5);self.assertIsNone(settled['asset_id'])
            self.assertAlmostEqual(w.os.media.spend()['by_kind']['video'],0.4,msg='A completed render is billed exactly once even when downloads fail')
            while len([j for j in w.store['media_jobs'] if j['status'] in ('pending','in_progress')])<3:w.os.media.start_video({'prompt':'Queue'})
            with self.assertRaisesRegex(scout.ScoutError,'already rendering'):w.os.media.start_video({'prompt':'One too many'})

    def test_catalogs_are_filtered_and_validated(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture)
            images=w.os.media.catalog('image');self.assertEqual([m['id'] for m in images],['google/gemini-3.1-flash-image','recraft/recraft-v4.1-vector'])
            self.assertTrue(images[1]['vector']);self.assertEqual(images[0]['aspect_ratios'],['1:1','16:9'])
            self.assertEqual(w.os.media.catalog('video')[0]['durations'],[4,6,8])
            self.assertEqual([m['id'] for m in w.os.media.catalog('chat')],['anthropic/claude-sonnet-5.5'])
            count=len(fixture.requests);w.os.media.catalog('image');self.assertEqual(len(fixture.requests),count,'Catalogs are cached')
            with self.assertRaises(scout.ScoutError):w.os.media.catalog('audio')

    def test_expert_roster_tools_and_settings(self):
        w=self.app()
        ids={e['id'] for e in EXPERTS};self.assertTrue({'engineer','designer','marketer'}<=ids)
        designer=w.os.hire_expert({'expert':'designer'});second=w.os.hire_expert({'expert':'designer','name':'Iris Two'})
        self.assertEqual(designer['employee_id'],'designer');self.assertEqual(second['employee_id'],'designer-2')
        w.os.hire_expert({'expert':'engineer'});w.os.hire_expert({'expert':'researcher'});w.os.hire_expert({'expert':'marketer'})
        names=lambda e:{d['name'] for d in w.os.definitions(e)}
        self.assertTrue({'generate_image','generate_video','list_brand_assets'}<=names('designer'))
        self.assertIn('build_web_page',names('designer'));self.assertIn('build_web_page',names('engineer'));self.assertNotIn('generate_video',names('engineer'))
        self.assertTrue({'generate_image','generate_video','build_web_page'}<=names('marketer'))
        self.assertFalse({'generate_image','generate_video','build_web_page'}&names('researcher'))
        tools={t['name'] for t in scout.load_json(w.employee_folder('designer')/'agent.json')['tools'] if t.get('name')}
        self.assertIn('generate_image',tools)
        with self.assertRaises(scout.ScoutError):w.os.hire_expert({'expert':'astronaut'})
        for bad in ({'image_model':'not a model'},{'video_model':''},{'media_budget_usd':0},{'media_budget_usd':True},{'media_budget_usd':20000}):
            with self.assertRaises(scout.ScoutError):w.os.save_settings(bad)
        w.os.save_settings({'image_model':'recraft/recraft-v4.1-vector','video_model':'bytedance/seedance-2.0-fast','media_budget_usd':25})
        self.assertEqual(w.os.settings['media_budget_usd'],25.0);self.assertEqual(w.os.media.spend()['budget'],25.0)

    def test_designer_tool_loop_generates_once_and_tracks_chat_cost(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture);w.os.hire_expert({'expert':'designer'})
            args={'prompt':'Instagram launch post with a croissant','purpose':'social_post','title':'Launch post'}
            fixture.calls=[('generate_image',args),('generate_image',args)]
            started=w.os.chat({'employee_id':'designer','message':'Create our launch post'});task=w.task(started['task_id']);self.wait(w,task)
            self.assertEqual(task['status'],'review',task.get('error'))
            self.assertEqual(len(fixture.posted('/v1/images')),1,'Identical tool calls reuse the saved receipt instead of paying twice')
            self.assertEqual(len(task['asset_ids']),1);asset=w.os.media.asset(task['asset_ids'][0])
            self.assertEqual(asset['task_id'],task['id']);self.assertEqual(asset['purpose'],'social_post');self.assertEqual(asset['aspect_ratio'],'4:5')
            self.assertAlmostEqual(task['cost'],3*0.0123,places=6)
            system=fixture.posted('/v1/chat/completions')[0]['messages'][0]['content']
            self.assertIn('{{asset:ID}}',system);self.assertIn('brand_kit',system)
            tools={t['function']['name'] for t in fixture.posted('/v1/chat/completions')[0]['tools']}
            self.assertIn('generate_video',tools)

    def test_pages_render_assets_in_a_sandbox_and_export(self):
        with OpenRouterFixture() as fixture:
            hub=OSHub(company.Workspace(self.folder));w=hub.root
            w.os.vault.set('openrouter','local-openrouter-fixture-key');w.os.http=ProviderHTTP(w.os.vault,fixture.url)
            image=w.os.media.generate_image({'prompt':'Hero','purpose':'banner'})['assets'][0]
            fixture.image=SVG;svg=w.os.media.generate_image({'prompt':'Vector logo','purpose':'logo'})['assets'][0]
            self.assertEqual(svg['mime'],'image/svg+xml')
            with self.assertRaisesRegex(scout.ScoutError,'Unknown asset'):w.os.media.save_web_page({'title':'Bad','html':'<html><body><img src="{{asset:'+'0'*24+'}}"></body></html>','page_type':'landing'})
            with self.assertRaises(scout.ScoutError):w.os.media.save_web_page({'title':'Fragment','html':'<div>no document</div>','page_type':'landing'})
            html='<!doctype html><html><body class="sk-loader-wrap"><h1>Bake</h1><img src="{{asset:'+image['id']+'}}"><script>fetch("/api/os/state")</script></body></html>'
            first=w.os.media.save_web_page({'title':'Opt-in','html':html,'page_type':'optin','funnel':'Launch','step':1})['asset']
            again=w.os.media.save_web_page({'title':'Opt-in','html':html,'page_type':'optin','funnel':'Launch','step':1})['asset']
            self.assertEqual((first['version'],again['version']),(1,2))
            rendered=w.os.media.render_page(w.os.media.asset(first['id'])).decode()
            self.assertIn('data:image/png;base64,',rendered);self.assertNotIn('{{asset:',rendered)
            server=ThreadingHTTPServer(('127.0.0.1',0),company.make_handler(w));server.daemon_threads=True
            threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url+'/api/os/asset?company=default&id='+first['id']) as r:
                    csp=r.headers['Content-Security-Policy'];body=r.read().decode()
                self.assertTrue(csp.startswith('sandbox allow-scripts'));self.assertNotIn('allow-same-origin',csp);self.assertIn("connect-src 'none'",csp)
                self.assertIn('data:image/png;base64,',body)
                with urlopen(url+'/api/os/asset?company=default&id='+svg['id']) as r:
                    self.assertTrue(r.headers['Content-Security-Policy'].startswith('sandbox;'));self.assertEqual(r.headers['Content-Type'],'image/svg+xml')
                with urlopen(url+'/api/os/asset?company=default&download=1&id='+image['id']) as r:
                    self.assertIn('attachment',r.headers['Content-Disposition']);self.assertEqual(r.read(),PNG)
                with urlopen(url+'/api/os/asset-export?company=default') as r:archive=zipfile.ZipFile(io.BytesIO(r.read()))
                manifest=json.loads(archive.read('manifest.json'));self.assertEqual(len(manifest),4)
                self.assertTrue(any(n.startswith('pages/') and n.endswith('.html') for n in archive.namelist()))
                for bad in ('/api/os/asset?company=default&id=../../company.json','/api/os/asset?company=nope&id='+image['id']):
                    with self.assertRaises(HTTPError) as caught:urlopen(url+bad)
                    self.assertEqual(caught.exception.code,404)
                with self.assertRaises(HTTPError) as caught:urlopen(Request(url+'/api/os/asset?company=default&id='+image['id'],headers={'Host':'evil.example'}))
                self.assertEqual(caught.exception.code,403)
                request=Request(url+'/api/os/action',data=json.dumps({'action':'delete_asset','data':{'id':image['id']}}).encode(),headers={'Content-Type':'application/json','X-Scout-Token':w.token},method='POST')
                with urlopen(request) as r:self.assertEqual(json.loads(r.read())['deleted'],image['id'])
                self.assertFalse(any(a['id']==image['id'] for a in w.store['assets']))
            finally:server.shutdown();server.server_close()

    def test_studio_logo_becomes_workspace_logo(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture);logo=w.os.media.generate_image({'prompt':'Mark','purpose':'logo'})['assets'][0]
            branding=w.os.save_branding({'logo_asset_id':logo['id']})
            self.assertNotIn('logo',branding);self.assertEqual(w.store['branding']['logo']['mime'],'image/png');self.assertEqual(w.os.logo_path().read_bytes(),PNG)
            fixture.image=SVG;vector=w.os.media.generate_image({'prompt':'Vector','purpose':'logo'})['assets'][0]
            with self.assertRaisesRegex(scout.ScoutError,'PNG, JPEG or WebP'):w.os.save_branding({'logo_asset_id':vector['id']})

    def test_restart_resumes_inflight_video_polling(self):
        with OpenRouterFixture() as fixture:
            w=self.app(fixture);job=w.os.media.start_video({'prompt':'Survives restart'})['job']
            with w.lock:w.store['media_jobs'][-1]['status']='downloading';w.persist()
            restarted=self.app(fixture)
            resumed=next(j for j in restarted.store['media_jobs'] if j['id']==job['id'])
            self.assertEqual((resumed['status'],resumed['next_poll']),('in_progress',0))

    def test_review_regressions(self):
        # A paused hosted Mentor task must not stop an older workspace from loading during the Mentor prompt upgrade.
        w=self.app()
        with w.lock:
            w.roles['mentor']['instructions']=w.roles['mentor']['instructions'][:200]+' (older stock text)'
            t=w.create_task({'employee_id':'mentor','title':'Hosted','description':'Hosted'},start=False);t.update(provider='openai',submitted=True,status='paused');w.persist()
        scout.save_json(self.folder/'employees.json',w.employees)
        reloaded=self.app();self.assertIn('mentor',reloaded.roles)
        with OpenRouterFixture() as fixture:
            w=self.app(fixture);w.os.hire_expert({'expert':'designer'})
            # Model-supplied keys outside the tool schema (model, quality) are dropped.
            fixture.calls=[('generate_image',{'prompt':'Post','purpose':'social_post','title':'Post','model':'openai/gpt-image-2','quality':'high'})]
            task=w.task(w.os.chat({'employee_id':'designer','message':'Post'})['task_id']);self.wait(w,task)
            sent=fixture.posted('/v1/images')[-1];self.assertEqual(sent['model'],'google/gemini-3.1-flash-image');self.assertNotIn('quality',sent)
            usage_rows=[r for r in w.store['usage'] if r['kind']=='image'];self.assertEqual(usage_rows[-1]['asset_ids'],task['asset_ids'])
            # A good poll resets transient failures.
            job=w.os.media.start_video({'prompt':'Long render'})['job']
            with w.lock:next(j for j in w.store['media_jobs'] if j['id']==job['id'])['failures']=4
            fixture.video_states=['in_progress'];w.os.media.poll_videos(clock=time.time()+60)
            self.assertEqual(self.wait_job(w,job['id'],('in_progress',))['failures'],0)
            # Pages never inline video; state snapshots keep raw studio rows server-side.
            fixture.video_states=['completed'];w.os.media.poll_videos(clock=time.time()+60);video=w.os.media.asset(self.wait_job(w,job['id'])['asset_id'])
            page=w.os.media.save_web_page({'title':'V','html':'<html><body><video src="{{asset:'+video['id']+'}}"></video></body></html>','page_type':'landing'})['asset']
            self.assertNotIn('data:video',w.os.media.render_page(w.os.media.asset(page['id'])).decode())
            hub=OSHub(w)
            state=hub.snapshot('default')
            for key in ('usage','assets','media_jobs','os_tool_receipts'):self.assertNotIn(key,state)
            self.assertEqual(len(state['operating_system']['studio']['assets']),3)
            server=ThreadingHTTPServer(('127.0.0.1',0),company.make_handler(w));server.daemon_threads=True
            threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(Request(url+'/api/os/asset?company=default&id='+video['id'],headers={'Range':'bytes=4-11'})) as r:
                    self.assertEqual(r.status,206);self.assertEqual(r.read(),MP4[4:12]);self.assertEqual(r.headers['Content-Range'],f'bytes 4-11/{len(MP4)}')
                    self.assertIn('immutable',r.headers['Cache-Control'])
                with self.assertRaises(HTTPError) as caught:urlopen(Request(url+'/api/os/asset?company=default&id='+video['id'],headers={'Range':'bytes=99999-'}))
                self.assertEqual(caught.exception.code,416)
            finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
