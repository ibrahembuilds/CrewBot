"""Provider HTTP via curl and per-company Windows protected credentials."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import scout

PROVIDERS={
 'openai':('https://api.openai.com/v1','OPENAI_API_KEY'),
 'openrouter':('https://openrouter.ai/api','OPENROUTER_API_KEY'),
 'tavily':('https://api.tavily.com','TAVILY_API_KEY'),
 'firecrawl':('https://api.firecrawl.dev','FIRECRAWL_API_KEY'),
 'resend':('https://api.resend.com','RESEND_API_KEY'),
 'github':('https://api.github.com','GITHUB_TOKEN'),
 'slack':('https://slack.com/api','SLACK_BOT_TOKEN')}

def protect(data,decrypt=False):
    if os.name!='nt':raise scout.ScoutError('Persistent secret storage requires Windows. Use session-only keys here.')
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte))); target=Blob()
    function=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    free=ctypes.windll.kernel32.LocalFree
    free.argtypes=[ctypes.c_void_p];free.restype=ctypes.c_void_p
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise scout.ScoutError('Windows could not unlock the saved credential. Enter it again under your Windows account.')
    try:return ctypes.string_at(target.pbData,target.cbData)
    finally:free(target.pbData)

class Vault:
    def __init__(self,folder,environment=True):
        self.path=Path(folder)/'credentials.dpapi.json';self.environment=environment;self.keys={};self.protected=set();self.disabled=set();self.lock=threading.RLock()
        if self.path.exists():
            try:
                saved=scout.load_json(self.path)
                if not isinstance(saved,dict):saved={}
                for name,value in saved.items():
                    if name not in PROVIDERS:continue
                    if value is None:self.disabled.add(name);continue
                    # One unreadable credential must not discard the other providers.
                    try:
                        self.keys[name]=protect(base64.b64decode(value,validate=True),True).decode()
                        self.protected.add(name)
                    except Exception:self.disabled.add(name)
            except Exception:self.disabled.update(PROVIDERS)
        for value in self.keys.values():scout.SECRETS.add(value)
    def get(self,name):
        if name not in PROVIDERS:raise scout.ScoutError('Unknown provider.')
        with self.lock:
            if name in self.disabled:return ''
            value=self.keys.get(name) or (os.environ.get(PROVIDERS[name][1],'').strip() if self.environment else '')
            if value:scout.SECRETS.add(value)
            return value
    def storage(self,name):
        """Non-secret source of the effective credential; configured is not verified."""
        if name not in PROVIDERS:raise scout.ScoutError('Unknown provider.')
        with self.lock:
            if name in self.disabled:return 'disabled'
            if name in self.keys:return 'protected' if name in self.protected else 'memory'
            return 'environment' if self.environment and os.environ.get(PROVIDERS[name][1],'').strip() else 'missing'
    def set(self,name,key,remember=False):
        if not isinstance(remember,bool):raise scout.ScoutError('Choose session-only or remembered storage.')
        if name not in PROVIDERS or not isinstance(key,str) or not 8<=len(key.strip())<=1000 or '\n' in key or '\r' in key:
            raise scout.ScoutError('Choose a provider and enter a valid API credential.')
        key=key.strip()
        with self.lock:
            if remember:
                saved=scout.load_json(self.path) if self.path.exists() else {}
                saved[name]=base64.b64encode(protect(key.encode())).decode()
                scout.save_json(self.path,saved)
            elif self.path.exists():
                saved=scout.load_json(self.path);saved.pop(name,None);scout.save_json(self.path,saved)
            self.keys[name]=key;self.disabled.discard(name)
            if remember:self.protected.add(name)
            else:self.protected.discard(name)
            scout.SECRETS.add(key)
        return {'provider':name,'configured':True,'storage':'Windows protected' if remember else 'this server session'}
    def remove(self,name):
        if name not in PROVIDERS:raise scout.ScoutError('Unknown provider.')
        with self.lock:
            saved=scout.load_json(self.path) if self.path.exists() else {}
            # A non-secret tombstone prevents an environment key silently returning.
            saved[name]=None;scout.save_json(self.path,saved)
            self.keys.pop(name,None);self.protected.discard(name);self.disabled.add(name)
        return {'provider':name,'configured':False,'storage':'disabled'}

class ProviderHTTP:
    def __init__(self,vault,local_origin=None):
        if local_origin and not re.fullmatch(r'http://127\.0\.0\.1:\d+',local_origin):raise scout.ScoutError('Test origin must be localhost.')
        self.vault=vault;self.local_origin=local_origin
    def request(self,provider,method,path,body=None,headers=None):
        with self.launch(provider,method,path,body,headers=headers) as result:return result
    def launch(self,provider,method,path,body=None,stream=False,headers=None):
        from contextlib import contextmanager
        @contextmanager
        def call():
            if provider not in PROVIDERS or not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or any(c.isspace() for c in path) or '\\' in path or '#' in path or method not in ('GET','POST','PUT','PATCH','DELETE'):
                raise scout.ScoutError('Invalid provider request.')
            if headers is not None and (not isinstance(headers,list) or any(not isinstance(h,str) or '\r' in h or '\n' in h or not re.fullmatch(r'[A-Za-z0-9-]+: [^\r\n]+',h) or h.split(':',1)[0].lower() in ('authorization','host','proxy-authorization') for h in headers)):
                raise scout.ScoutError('Invalid provider headers.')
            key=self.vault.get(provider)
            if not key:raise scout.ScoutError('Add the '+provider+' API key in Settings.')
            if any(c in key for c in ('\r','\n','\x00')):raise scout.ScoutError('Re-enter the provider credential in Settings.')
            curl=shutil.which('curl.exe') or shutil.which('curl')
            if not curl:raise scout.ScoutError('curl is required.')
            with tempfile.TemporaryDirectory(prefix='company-provider-') as temporary:
                folder=Path(temporary);header_file=folder/'headers.txt'
                args=[curl,'-q','--silent','--show-error','--no-buffer','--connect-timeout','10','--max-time','180','--max-filesize','4000000','--config','-','--request',method,'--dump-header',str(header_file),(self.local_origin or PROVIDERS[provider][0])+path]
                if body is not None:
                    payload=folder/'request.json';payload.write_text(json.dumps(body,ensure_ascii=False),encoding='utf-8');args+=['--data-binary','@'+str(payload)]
                values=['Authorization: Bearer '+key,'Content-Type: application/json','Accept: '+('text/event-stream' if stream else 'application/json')]
                if provider=='github':values+=['Accept: application/vnd.github+json','X-GitHub-Api-Version: 2022-11-28','User-Agent: CrewBot']
                values+=headers or []
                config='\n'.join('header = '+scout.curl_quote(h) for h in values)+'\n'
                if not stream:args+=['--write-out','\n%{http_code}']
                process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace')
                observer=None
                try:
                    if stream:
                        process.stdin.write(config);process.stdin.close()
                        observer=scout.CurlStream(process,header_file,185)
                        try:observer.wait_ready()
                        except scout.ScoutError as exc:
                            raise scout.ScoutError(scout.redact(provider+' stream: '+str(exc).split('\n')[0])) from None
                        yield observer
                    else:
                        output,error=process.communicate(config,timeout=185)
                        raw,_,status=output.rpartition('\n')
                        if process.returncode or not status.isdigit():raise scout.ScoutError('Provider connection interrupted; inspect external actions before retrying.')
                        try:value=json.loads(raw)
                        except ValueError:raise scout.ScoutError(f'{provider} HTTP {status}: provider returned an invalid JSON response.') from None
                        if not 200<=int(status)<300 or (isinstance(value,dict) and (value.get('error') or value.get('success') is False or value.get('ok') is False)):
                            raise scout.ScoutError(scout.redact(f'{provider} HTTP {status}: '+json.dumps(value)[:2000]))
                        yield value
                except subprocess.TimeoutExpired:
                    raise scout.ScoutError('Provider timed out. External writes are not automatically retried.') from None
                finally:
                    if observer:observer.close()
                    elif process.poll() is None:process.kill();process.wait(timeout=5)
                    for pipe in (process.stdin,process.stdout,process.stderr):
                        if pipe and not pipe.closed:pipe.close()
        return call()

def complete(http,model,messages,tools,on_text,cancelled,max_rounds=8):
    """Bounded streamed OpenRouter tool loop. A disconnect is surfaced, never replayed."""
    if not isinstance(max_rounds,int) or isinstance(max_rounds,bool) or not 1<=max_rounds<=12:raise scout.ScoutError('Choose a tool-call limit from 1 to 12.')
    executed={}
    for _ in range(max_rounds):
        if cancelled():raise scout.ScoutError('Conversation stopped.')
        definitions=[{'type':'function','function':definition} for definition,handler in tools.values()]
        payload={'model':model,'messages':messages,'stream':True,'max_tokens':6000}
        if definitions:payload['tools']=definitions
        text='';calls={};finished=False;finish_reason=None
        with http.launch('openrouter','POST','/v1/chat/completions',payload,stream=True) as stream:
            for event in stream.events(cancelled):
                if cancelled():raise scout.ScoutError('Conversation stopped.')
                if event.get('error'):raise scout.ScoutError('OpenRouter stream error: '+scout.redact(event['error']))
                choices=event.get('choices') or []
                if not choices:continue
                if not isinstance(choices,list) or not isinstance(choices[0],dict):raise scout.ScoutError('Malformed OpenRouter choices.')
                choice=choices[0];delta=choice.get('delta') or {}
                if not isinstance(delta,dict):raise scout.ScoutError('Malformed OpenRouter delta.')
                if choice.get('finish_reason')=='error':raise scout.ScoutError('OpenRouter generation failed.')
                if choice.get('finish_reason'):finished=True;finish_reason=choice['finish_reason']
                part=delta.get('content') or ''
                if isinstance(part,str) and part:text+=part;on_text(scout.redact(part))
                for call in delta.get('tool_calls') or []:
                    if not isinstance(call,dict):raise scout.ScoutError('Malformed streamed tool call.')
                    index=call.get('index',0)
                    if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<16 or call.get('type','function')!='function':raise scout.ScoutError('Unsupported streamed tool call.')
                    entry=calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
                    if call.get('id'):
                        if not isinstance(call['id'],str) or (entry['id'] and entry['id']!=call['id']):raise scout.ScoutError('Inconsistent streamed tool-call ID.')
                        entry['id']=call['id']
                    function=call.get('function') or {}
                    if not isinstance(function,dict):raise scout.ScoutError('Malformed tool-call function.')
                    for key in ('name','arguments'):
                        part=function.get(key) or ''
                        if not isinstance(part,str):raise scout.ScoutError('Malformed tool-call argument fragment.')
                        entry['function'][key]+=part
                    if len(entry['function']['name'])>64 or len(entry['function']['arguments'])>100000:raise scout.ScoutError('Tool-call payload exceeded its limit.')
        if not finished:raise scout.ScoutError('OpenRouter stream ended without a completion signal. Partial output was retained.')
        if finish_reason in ('length','content_filter'):raise scout.ScoutError('OpenRouter output was truncated or filtered. Partial output was retained; no tool calls were executed.')
        if finish_reason not in ('stop','tool_calls') or (calls and finish_reason!='tool_calls') or (not calls and finish_reason!='stop'):
            raise scout.ScoutError('OpenRouter returned an inconsistent completion signal. Partial output was retained; no tool calls were executed.')
        if not calls:
            if not text.strip():raise scout.ScoutError('OpenRouter returned no text or tool calls.')
            return text
        ordered=[calls[i] for i in sorted(calls)]
        identifiers=set();arguments={}
        # Validate the complete batch before allowing its first side effect.
        for call in ordered:
            identifier=call['id'];name=call['function']['name']
            if not identifier or len(identifier)>256 or identifier in identifiers or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name):raise scout.ScoutError('Invalid or duplicate tool-call identity; no tools were executed.')
            identifiers.add(identifier)
            try:args=json.loads(call['function']['arguments'])
            except ValueError:raise scout.ScoutError('Malformed tool-call JSON; no tools were executed.') from None
            if not isinstance(args,dict):raise scout.ScoutError('Tool arguments must be a JSON object; no tools were executed.')
            arguments[identifier]=args
            if identifier in executed and executed[identifier][0]!=(name,args):raise scout.ScoutError('A tool-call ID was reused with different arguments; inspect saved work.')
        messages.append({'role':'assistant','content':text or None,'tool_calls':ordered})
        for call in ordered:
            if cancelled():raise scout.ScoutError('Conversation stopped.')
            identifier=call['id'];name=call['function']['name'];args=arguments[identifier]
            try:
                if name not in tools:raise scout.ScoutError('This employee cannot use '+name)
                if identifier in executed:result=executed[identifier][1]
                else:
                    result=tools[name][1](args)
                    executed[identifier]=((name,args),result)
            except Exception as exc:result={'error':scout.redact(exc)}
            executed[identifier]=((name,args),result)
            messages.append({'role':'tool','tool_call_id':call['id'],'content':scout.redact(json.dumps(result,ensure_ascii=False))})
    raise scout.ScoutError('Tool-call limit reached. Review saved work before continuing.')
