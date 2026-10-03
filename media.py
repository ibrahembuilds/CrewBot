"""Brand asset studio: OpenRouter Image and Video APIs, landing pages/funnels, spend tracking and budgets.

Images use POST /v1/images (synchronous, base64 output). Videos use the asynchronous
POST /v1/videos → GET /v1/videos/{id} → GET /v1/videos/{id}/content flow. Every cost
recorded here is the provider-reported usage.cost; nothing is estimated or invented.
"""
import base64
import binascii
import copy
from datetime import datetime,timezone
import hashlib
import json
import math
import re
import tempfile
import threading
import time
import uuid
import zipfile
import scout
from operations import text,stamp

# Purpose presets: the aspect ratio a business normally needs for each deliverable.
PURPOSES={
 'logo':('1:1','Logo mark. Clean, simple, scalable, centered on a plain background, no mockup, no extra text beyond the brand name if requested.'),
 'social_post':('4:5','Social media feed post (Instagram/Facebook/LinkedIn). Scroll-stopping composition with clear focal point.'),
 'story':('9:16','Vertical story/reel cover. Leave safe space at top and bottom for platform UI.'),
 'poster':('2:3','Poster. Strong visual hierarchy, headline area, print-ready composition.'),
 'banner':('16:9','Website hero / ad banner. Wide composition with space for headline copy.'),
 'ad':('1:1','Paid social ad creative. Clear product or offer focus, high contrast.'),
 'product_shot':('1:1','Professional product photograph, studio lighting, commercial quality.'),
 'illustration':('1:1','Illustration in a consistent brand style.'),
 'other':('1:1','')}
ASPECTS=('auto','1:1','16:9','9:16','4:3','3:4','3:2','2:3','4:5','5:4','21:9','9:21')
IMAGE_TYPES={'image/png':'png','image/jpeg':'jpg','image/webp':'webp','image/svg+xml':'svg','image/gif':'gif'}
VIDEO_TYPES={'video/mp4':'mp4','video/webm':'webm','video/quicktime':'mov'}
MODEL_ID=re.compile(r'~?[a-z0-9][a-z0-9._-]{0,60}/[a-z0-9][a-z0-9._:-]{0,100}')
ASSET_REF=re.compile(r'\{\{asset:([a-f0-9]{24})\}\}')
MAX_IMAGE_RESPONSE=90_000_000
MAX_VIDEO_BYTES=400_000_000
MAX_PAGE_BYTES=600_000
MAX_INLINE_BYTES=8_000_000
POLL_SECONDS=20
MEDIA_DEFAULTS={'image_model':'google/gemini-3.1-flash-image','video_model':'google/veo-3.1-lite','media_budget_usd':10.0}

def sniff_image(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if len(raw)>=12 and raw[:4]==b'RIFF' and raw[8:12]==b'WEBP':return 'image/webp'
    if raw[:6] in (b'GIF87a',b'GIF89a'):return 'image/gif'
    head=raw[:2000].lstrip().lower()
    if head.startswith(b'<svg') or (head.startswith(b'<?xml') and b'<svg' in head):return 'image/svg+xml'
    return None

def sniff_video(path):
    with open(path,'rb') as f:head=f.read(16)
    if len(head)>=12 and head[4:8]==b'ftyp':return 'video/quicktime' if head[8:10]==b'qt' else 'video/mp4'
    if head.startswith(b'\x1aE\xdf\xa3'):return 'video/webm'
    return None

def valid_cost(value):
    """Provider-reported USD cost, or None when absent or not a finite non-negative number."""
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:return None
    return float(value)

def month_key(value=None):
    return (value or datetime.now(timezone.utc)).strftime('%Y-%m')

class Media:
    def __init__(self,app):
        self.app=app;self.w=app.w;self.catalogs={};self.polling=set();self.poll_lock=threading.Lock();self.submit_lock=threading.Lock()
        with self.w.lock:
            for key,value in MEDIA_DEFAULTS.items():self.app.settings.setdefault(key,value)
            for key in ('assets','media_jobs','usage'):self.w.store.setdefault(key,[])
            for job in self.w.store['media_jobs']:
                if job['status'] in ('pending','in_progress','downloading'):job['next_poll']=0
                if job['status']=='downloading':job['status']='in_progress'
    @property
    def folder(self):return self.w.folder/'assets'
    # ---------- spend ----------
    def record_usage(self,kind,usage,model,task=None,asset_ids=None):
        cost=valid_cost(usage.get('cost')) if isinstance(usage,dict) else None
        if cost is None:return 0.0
        with self.w.lock:
            self.w.store['usage'].append({'time':stamp(),'month':month_key(),'kind':kind,'model':model,'cost':cost,'task_id':task['id'] if task else None,'employee_id':task['employee_id'] if task else None,'asset_ids':asset_ids if asset_ids is not None else []})
            self.w.store['usage']=self.w.store['usage'][-5000:];self.w.changed()
        return float(cost)
    def spend(self):
        current=month_key();totals={'chat':0.0,'image':0.0,'video':0.0}
        with self.w.lock:
            for item in self.w.store['usage']:
                if item.get('month')==current:totals[item['kind']]=totals.get(item['kind'],0.0)+item['cost']
        return {'month':current,'total':round(sum(totals.values()),6),'by_kind':{k:round(v,6) for k,v in totals.items()},'budget':self.app.settings['media_budget_usd']}
    def check_budget(self):
        budget=self.app.settings['media_budget_usd']
        if budget and self.spend()['total']>=budget:
            raise scout.ScoutError(f'Monthly AI budget of ${budget:.2f} reached. Raise it in Settings → Models & operating limits to continue generating.')
    # ---------- catalogs ----------
    def catalog(self,kind):
        if kind not in ('image','video','chat'):raise scout.ScoutError('Choose an image, video or chat model catalog.')
        cached=self.catalogs.get(kind)
        if cached and cached[0]>time.time()-3600:return cached[1]
        path={'image':'/v1/images/models','video':'/v1/videos/models','chat':'/v1/models'}[kind]
        value=self.app.http.request('openrouter','GET',path,max_bytes=30_000_000,max_time=60)
        rows=value.get('data') if isinstance(value,dict) else None
        if not isinstance(rows,list):raise scout.ScoutError('OpenRouter returned an unexpected model catalog.')
        models=[]
        for m in rows:
            if not isinstance(m,dict) or not isinstance(m.get('id'),str) or not MODEL_ID.fullmatch(m['id']):continue
            if kind=='image':
                params=m.get('supported_parameters') if isinstance(m.get('supported_parameters'),dict) else {}
                aspects=params.get('aspect_ratio',{}).get('values') if isinstance(params.get('aspect_ratio'),dict) else None
                models.append({'id':m['id'],'name':str(m.get('name') or m['id'])[:120],'inputs':(m.get('architecture') or {}).get('input_modalities') or [],'parameters':sorted(params),'aspect_ratios':aspects if isinstance(aspects,list) else None,'vector':'svg' in json.dumps(params.get('output_format',{}))})
            elif kind=='video':
                models.append({'id':m['id'],'name':str(m.get('name') or m['id'])[:120],'durations':m.get('supported_durations') if isinstance(m.get('supported_durations'),list) else None,'resolutions':m.get('supported_resolutions') if isinstance(m.get('supported_resolutions'),list) else None,'aspect_ratios':m.get('supported_aspect_ratios') if isinstance(m.get('supported_aspect_ratios'),list) else None,'pricing':{str(k)[:60]:str(v)[:20] for k,v in (m.get('pricing_skus') or {}).items()} if isinstance(m.get('pricing_skus'),dict) else {}})
            else:
                if 'tools' not in (m.get('supported_parameters') or []):continue
                pricing=m.get('pricing') if isinstance(m.get('pricing'),dict) else {}
                models.append({'id':m['id'],'name':str(m.get('name') or m['id'])[:120],'context':m.get('context_length'),'prompt':pricing.get('prompt'),'completion':pricing.get('completion')})
        self.catalogs[kind]=(time.time(),models)
        return models
    # ---------- brand ----------
    def brand_context(self):
        company=scout.load_json(self.w.folder/'company.json')
        with self.w.lock:branding=copy.deepcopy(self.w.store.get('branding',{}))
        parts=[]
        if company.get('name'):parts.append('Brand: '+company['name'])
        if company.get('summary'):parts.append('Offering: '+company['summary'][:400])
        if company.get('market'):parts.append('Audience: '+company['market'][:300])
        colors=[branding.get(k) for k in ('accent','navigation','background') if branding.get(k)]
        if colors:parts.append('Brand colors: '+', '.join(colors))
        if branding.get('visual_style'):parts.append('Visual style: '+branding['visual_style'])
        if branding.get('brand_voice'):parts.append('Brand voice: '+branding['brand_voice'])
        return '\n'.join(parts)
    # ---------- assets ----------
    def asset(self,identifier):
        with self.w.lock:
            found=next((a for a in self.w.store['assets'] if a['id']==identifier),None)
        if not found:raise scout.ScoutError('Asset not found in this company.')
        return found
    def asset_path(self,item):
        filename=item.get('filename','')
        if not re.fullmatch(r'[a-f0-9]{24,64}\.(png|jpg|webp|svg|gif|mp4|webm|mov|html)',filename):raise scout.ScoutError('Invalid asset file.')
        folder=self.folder.resolve();path=(folder/filename).resolve()
        if path.parent!=folder or not path.is_file():raise scout.ScoutError('Asset file is missing.')
        return path
    def data_url(self,identifier,kinds=('image',)):
        item=self.asset(identifier)
        if item['kind'] not in kinds:raise scout.ScoutError('Reference an image asset.')
        return 'data:'+item['mime']+';base64,'+base64.b64encode(self.asset_path(item).read_bytes()).decode()
    def add_asset(self,raw,mime,meta,task=None):
        digest=hashlib.sha256(raw).hexdigest();identifier=uuid.uuid4().hex[:24]
        extension=IMAGE_TYPES.get(mime) or VIDEO_TYPES.get(mime) or ('html' if mime=='text/html' else None)
        if not extension:raise scout.ScoutError('Unsupported asset type.')
        self.folder.mkdir(parents=True,exist_ok=True);filename=identifier+'.'+extension
        (self.folder/filename).write_bytes(raw)
        item={'id':identifier,'filename':filename,'mime':mime,'bytes':len(raw),'sha256':digest,'created_at':stamp(),'task_id':task['id'] if task else None,'employee_id':task['employee_id'] if task else None,**meta}
        with self.w.lock:
            self.w.store['assets'].append(item)
            if task is not None:task.setdefault('asset_ids',[]).append(identifier)
            self.w.changed()
        return item
    def delete_asset(self,identifier):
        item=self.asset(identifier)
        try:self.asset_path(item).unlink()
        except scout.ScoutError:pass
        with self.w.lock:
            self.w.store['assets']=[a for a in self.w.store['assets'] if a['id']!=identifier];self.w.log('Asset deleted: '+item.get('title','asset'));self.w.changed()
        return {'deleted':identifier}
    def public(self,item):
        return {k:v for k,v in item.items() if k not in ('filename','sha256')}
    # ---------- images ----------
    def generate_image(self,body,task=None):
        if not isinstance(body,dict):raise scout.ScoutError('Image request must be an object.')
        prompt=text(body.get('prompt'),4000)
        purpose=body.get('purpose') or 'other'
        if purpose not in PURPOSES:raise scout.ScoutError('Choose a supported image purpose.')
        aspect=body.get('aspect_ratio') or PURPOSES[purpose][0]
        if aspect not in ASPECTS:raise scout.ScoutError('Choose a supported aspect ratio.')
        model=body.get('model') or self.app.settings['image_model']
        if not isinstance(model,str) or not MODEL_ID.fullmatch(model):raise scout.ScoutError('Choose an OpenRouter image model ID.')
        count=body.get('n',1)
        if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=4:raise scout.ScoutError('Generate 1–4 images at a time.')
        quality=body.get('quality') or None
        if quality not in (None,'auto','low','medium','high'):raise scout.ScoutError('Choose auto, low, medium or high quality.')
        background=body.get('background') or None
        if background not in (None,'auto','transparent','opaque'):raise scout.ScoutError('Choose an auto, transparent or opaque background.')
        references=body.get('reference_asset_ids') or []
        if not isinstance(references,list) or len(references)>4 or not all(isinstance(r,str) for r in references):raise scout.ScoutError('Use up to four reference images.')
        title=text(body.get('title') or (purpose.replace('_',' ').title()+' — '+prompt[:60]),160)
        use_brand=body.get('use_brand',True)
        if not isinstance(use_brand,bool):raise scout.ScoutError('Invalid brand setting.')
        brand=self.brand_context() if use_brand else ''
        full=prompt
        if PURPOSES[purpose][1]:full+='\n\nFormat: '+PURPOSES[purpose][1]
        if brand:full+='\n\nBrand guidelines:\n'+brand
        payload={'model':model,'prompt':full[:12000],'aspect_ratio':aspect}
        if count>1:payload['n']=count
        if quality:payload['quality']=quality
        if background:payload['background']=background
        if references:payload['input_references']=[{'type':'image_url','image_url':{'url':self.data_url(r)}} for r in references]
        self.check_budget()
        self.w.log(f'Generating {count} {purpose.replace("_"," ")} image(s) with {model}',task)
        value=self.app.http.request('openrouter','POST','/v1/images',payload,max_bytes=MAX_IMAGE_RESPONSE,max_time=300)
        usage=value.get('usage') if isinstance(value,dict) and isinstance(value.get('usage'),dict) else {}
        # The provider bills a 2xx generation even if its payload turns out unusable, so record spend first.
        cost=valid_cost(usage.get('cost'));asset_ids=[]
        self.record_usage('image',usage,model,task,asset_ids)
        images=value.get('data') if isinstance(value,dict) else None
        if not isinstance(images,list) or not images:raise scout.ScoutError('OpenRouter returned no images. No asset was saved.')
        decoded=[]
        for image in images[:count]:
            if not isinstance(image,dict) or not isinstance(image.get('b64_json'),str):raise scout.ScoutError('OpenRouter returned a malformed image.')
            try:raw=base64.b64decode(image['b64_json'],validate=True)
            except (binascii.Error,ValueError):raise scout.ScoutError('OpenRouter returned invalid image data.') from None
            mime=sniff_image(raw)
            if not mime:raise scout.ScoutError('OpenRouter returned an unrecognized image format.')
            decoded.append((raw,mime))
        saved=[]
        for index,(raw,mime) in enumerate(decoded):
            saved.append(self.add_asset(raw,mime,{'kind':'image','purpose':purpose,'title':title if len(decoded)==1 else f'{title} ({index+1})','prompt':prompt,'model':model,'aspect_ratio':aspect,'brand_applied':bool(brand),'cost':round(cost/len(decoded),6) if cost is not None else None},task))
        with self.w.lock:asset_ids.extend(a['id'] for a in saved);self.w.changed()
        self.w.log(f'{len(saved)} image asset(s) saved'+(f' · ${cost:.4f}' if cost is not None else ''),task)
        return {'assets':[self.public(a) for a in saved],'cost':cost,'embed':['{{asset:'+a['id']+'}}' for a in saved]}
    # ---------- videos ----------
    def start_video(self,body,task=None):
        if not isinstance(body,dict):raise scout.ScoutError('Video request must be an object.')
        prompt=text(body.get('prompt'),4000)
        model=body.get('model') or self.app.settings['video_model']
        if not isinstance(model,str) or not MODEL_ID.fullmatch(model):raise scout.ScoutError('Choose an OpenRouter video model ID.')
        payload={'model':model}
        duration=body.get('duration')
        if duration not in (None,''):
            if isinstance(duration,bool) or not isinstance(duration,int) or not 1<=duration<=30:raise scout.ScoutError('Choose a 1–30 second duration.')
            payload['duration']=duration
        resolution=body.get('resolution') or None
        if resolution and resolution not in ('480p','720p','768p','1080p','1K','2K','4K'):raise scout.ScoutError('Choose a supported resolution.')
        if resolution:payload['resolution']=resolution
        aspect=body.get('aspect_ratio') or None
        if aspect and aspect not in ('16:9','9:16','1:1','4:3','3:4','3:2','2:3','21:9','9:21'):raise scout.ScoutError('Choose a supported video aspect ratio.')
        if aspect:payload['aspect_ratio']=aspect
        audio=body.get('generate_audio')
        if audio is not None:
            if not isinstance(audio,bool):raise scout.ScoutError('Invalid audio setting.')
            payload['generate_audio']=audio
        if body.get('first_frame_asset_id'):
            payload['frame_images']=[{'type':'image_url','image_url':{'url':self.data_url(body['first_frame_asset_id'])},'frame_type':'first_frame'}]
        title=text(body.get('title') or ('Video — '+prompt[:60]),160)
        use_brand=body.get('use_brand',True)
        if not isinstance(use_brand,bool):raise scout.ScoutError('Invalid brand setting.')
        brand=self.brand_context() if use_brand else ''
        payload['prompt']=(prompt+('\n\nBrand guidelines:\n'+brand if brand else ''))[:8000]
        with self.submit_lock:
            return self.submit_video(payload,title,prompt,model,task)
    def submit_video(self,payload,title,prompt,model,task):
        with self.w.lock:
            active=[j for j in self.w.store['media_jobs'] if j['status'] in ('pending','in_progress','downloading')]
            if len(active)>=3:raise scout.ScoutError('Three videos are already rendering. Wait for one to finish.')
        self.check_budget()
        value=self.app.http.request('openrouter','POST','/v1/videos',payload,max_time=120)
        job_id=value.get('id') if isinstance(value,dict) else None
        if not isinstance(job_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',job_id):raise scout.ScoutError('OpenRouter did not return a valid video job ID.')
        job={'id':uuid.uuid4().hex[:24],'provider_job_id':job_id,'status':value.get('status') if value.get('status') in ('pending','in_progress') else 'pending','title':title,'prompt':prompt,'model':model,'params':{k:v for k,v in payload.items() if k not in ('prompt','frame_images','model')},'created_at':stamp(),'next_poll':time.time()+POLL_SECONDS,'task_id':task['id'] if task else None,'employee_id':task['employee_id'] if task else None,'asset_id':None,'error':None,'cost':None}
        with self.w.lock:self.w.store['media_jobs'].append(job);self.w.log('Video render submitted: '+title,task)
        return {'job':copy.deepcopy(job),'note':'Video renders asynchronously (usually 30 s – several minutes). It appears in Studio when complete.'}
    def poll_videos(self,clock=None):
        clock=clock or time.time()
        with self.w.lock:
            due=[j['id'] for j in self.w.store['media_jobs'] if j['status'] in ('pending','in_progress') and j.get('next_poll',0)<=clock]
        for identifier in due:
            with self.poll_lock:
                if identifier in self.polling:continue
                self.polling.add(identifier)
            threading.Thread(target=self.poll_job,args=(identifier,),daemon=True).start()
    def poll_job(self,identifier):
        try:
            with self.w.lock:job=next(j for j in self.w.store['media_jobs'] if j['id']==identifier)
            value=self.app.http.request('openrouter','GET','/v1/videos/'+job['provider_job_id'],max_time=60,error_field_ok=True)
            status=value.get('status')
            if status in ('pending','in_progress'):
                with self.w.lock:job.update(status=status,next_poll=time.time()+POLL_SECONDS,failures=0,error=None);self.w.changed()
                return
            if status!='completed':
                with self.w.lock:job.update(status='failed',error=scout.redact(str(value.get('error') or status or 'Video generation failed'))[:1000]);self.w.log('Video render failed: '+job['title'])
                return
            usage=value.get('usage') if isinstance(value.get('usage'),dict) else {}
            task=None
            with self.w.lock:
                if job.get('task_id'):
                    try:task=self.w.task(job['task_id'])
                    except scout.ScoutError:task=None
                first=not job.get('billed')
                job.update(status='downloading',billed=True)
                cost=valid_cost(usage.get('cost'));job['cost']=cost;self.w.changed()
            # Billed once on completion even if the download later fails.
            if first:self.record_usage('video',usage,job['model'],task)
            temporary=self.folder/('download-'+identifier+'.bin')
            self.app.http.download('openrouter','/v1/videos/'+job['provider_job_id']+'/content?index=0',temporary,MAX_VIDEO_BYTES)
            mime=sniff_video(temporary)
            if not mime:
                temporary.unlink(missing_ok=True);raise scout.ScoutError('Downloaded video is not a recognized MP4/WebM/MOV file.')
            raw=temporary.read_bytes();temporary.unlink(missing_ok=True)
            item=self.add_asset(raw,mime,{'kind':'video','purpose':'video','title':job['title'],'prompt':job['prompt'],'model':job['model'],'params':job['params'],'brand_applied':True,'cost':cost},task)
            with self.w.lock:job.update(status='completed',asset_id=item['id'],cost=cost);self.w.log('Video ready: '+job['title']+(f' · ${cost:.4f}' if cost is not None else ''))
        except Exception as exc:
            with self.w.lock:
                job=next((j for j in self.w.store['media_jobs'] if j['id']==identifier),None)
                if job:
                    job['failures']=job.get('failures',0)+1
                    # Transient polling errors retry; repeated failures stop so they cannot loop forever.
                    if job['failures']>=5:job.update(status='failed',error=scout.redact(exc)[:1000])
                    else:job.update(status='in_progress',error=scout.redact(exc)[:300],next_poll=time.time()+POLL_SECONDS*job['failures'])
                    self.w.changed()
        finally:
            with self.poll_lock:self.polling.discard(identifier)
    # ---------- web pages & funnels ----------
    def save_web_page(self,body,task=None):
        if not isinstance(body,dict):raise scout.ScoutError('Page must be an object.')
        title=text(body.get('title'),160)
        html=body.get('html')
        if not isinstance(html,str) or not html.strip() or len(html.encode())>MAX_PAGE_BYTES:raise scout.ScoutError('Provide complete HTML up to 600 KB.')
        # Only configured credentials are rejected; the generic key regex misfires on CSS class names.
        if any(len(k)>=8 and k in html for k in tuple(scout.SECRETS)):raise scout.ScoutError('Page appears to contain an API key. Remove credentials from the page.')
        if '<html' not in html.lower() and '<body' not in html.lower():raise scout.ScoutError('Provide a complete standalone HTML document with <html> and <body>.')
        with self.w.lock:known={a['id'] for a in self.w.store['assets']}
        missing=[ref for ref in ASSET_REF.findall(html) if ref not in known]
        if missing:raise scout.ScoutError('Unknown asset references: '+', '.join(missing[:5])+'. Use IDs from list_brand_assets or generate_image.')
        funnel=body.get('funnel') or ''
        if funnel:funnel=text(funnel,80)
        step=body.get('step',1)
        if isinstance(step,bool) or not isinstance(step,int) or not 1<=step<=10:raise scout.ScoutError('Funnel steps are numbered 1–10.')
        kind=body.get('page_type') or 'landing'
        if kind not in ('landing','sales','optin','thankyou','checkout','webinar','website','email'):raise scout.ScoutError('Choose a supported page type.')
        with self.w.lock:
            versions=[a for a in self.w.store['assets'] if a['kind']=='page' and a.get('title')==title and a.get('funnel','')==funnel and a.get('step')==step]
        item=self.add_asset(html.encode(),'text/html',{'kind':'page','purpose':kind,'title':title,'funnel':funnel,'step':step,'version':len(versions)+1,'prompt':text(body.get('notes') or title,2000),'model':None,'cost':None},task)
        self.w.log('Web page saved: '+title+(f' (funnel {funnel}, step {step})' if funnel else ''),task)
        return {'asset':self.public(item),'preview':'Open Studio to preview this page; it renders in an isolated sandbox.'}
    def render_page(self,item):
        html=self.asset_path(item).read_text(encoding='utf-8')
        def inline(match):
            try:
                item=self.asset(match[1])
                if item['bytes']>MAX_INLINE_BYTES:return ''
                return self.data_url(match[1],('image',))
            except scout.ScoutError:return ''
        return ASSET_REF.sub(inline,html).encode()
    def export_zip(self):
        """Write all assets to a temporary zip on disk; the caller streams and closes the returned file."""
        handle=tempfile.TemporaryFile();manifest=[]
        with self.w.lock:items=copy.deepcopy(self.w.store['assets'])
        with zipfile.ZipFile(handle,'w',zipfile.ZIP_DEFLATED) as archive:
            for item in items:
                try:
                    raw=self.render_page(item) if item['kind']=='page' else self.asset_path(item).read_bytes()
                except (scout.ScoutError,OSError):continue
                safe=re.sub(r'[^A-Za-z0-9._-]+','-',item.get('title','asset'))[:60].strip('-') or 'asset'
                name=f"{item['kind']}s/{safe}-{item['id'][:8]}.{item['filename'].rsplit('.',1)[1]}"
                archive.writestr(name,raw);manifest.append({**self.public(item),'file':name})
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        handle.seek(0)
        return handle
    def list_assets(self,kind=None):
        with self.w.lock:items=[self.public(a) for a in self.w.store['assets'] if not kind or a['kind']==kind][-60:]
        return {'assets':[{k:a.get(k) for k in ('id','kind','purpose','title','mime','aspect_ratio','funnel','step','created_at')}|{'embed':'{{asset:'+a['id']+'}}'} for a in items]}
    def snapshot(self):
        with self.w.lock:
            return {'assets':[self.public(a) for a in self.w.store['assets']],'jobs':copy.deepcopy(self.w.store['media_jobs'][-50:]),'spend':self.spend(),'purposes':{k:v[0] for k,v in PURPOSES.items()},'aspects':list(ASPECTS)}
