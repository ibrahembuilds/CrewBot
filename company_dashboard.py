"""CrewBot employee workspace. Python stdlib; OpenAI traffic uses curl."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlparse, parse_qs, quote
import uuid
import webbrowser
import scout
from operations import Operations

ROOT = Path(__file__).resolve().parent
def now():
    return datetime.now(timezone.utc).isoformat()

def function(name, description, properties=None):
    properties = properties or {}
    return {"type":"function", "name":name, "description":description,
            "parameters":{"type":"object", "properties":properties, "required":list(properties), "additionalProperties":False}}

class Workspace:
    def __init__(self, folder=ROOT, api_factory=scout.CurlAPI):
        self.folder, self.api_factory = Path(folder), api_factory
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.token = secrets.token_urlsafe(32)
        self.events, self.sequence = [], 0
        self.employees = scout.load_json(self.folder / "employees.json")
        self.roles = {e["id"]:e for e in self.employees}
        self.employee_locks = {k:threading.Lock() for k in self.roles}
        self.active = set()
        self.path = self.folder / "company-state.json"
        self.store = scout.load_json(self.path) if self.path.exists() else {"tasks":[], "activity":[]}
        for task in self.store["tasks"]:
            if task["status"] in ("running", "queued"):
                task["status"] = "paused"
                task["error"] = "Server restarted. Resume observation before submitting more work."
        original = scout.load_json(self.folder / "agent.json")
        for employee in self.employees:
            folder = self.employee_folder(employee["id"])
            folder.mkdir(parents=True, exist_ok=True)
            if not (folder / "agent.json").exists():
                config = copy.deepcopy(original)
                config["name"] = original["name"] if employee["id"] == "growth" else "CrewBot — " + employee["role"]
                config["instructions"] = employee["instructions"] + "\n\n" + (original["instructions"] if employee["id"] == "growth" else "Use the shared company context. Put downloadable files in /workspace/outputs. Explain findings with concise evidence-based rationale.")
                config["tools"] += [function("get_company_context", "Read this company's current business knowledge."), function("list_company_tasks", "Read current company tasks and their deliverables.")]
                if employee["id"] == "director":
                    config["tools"].append(function("delegate_task", "Assign a bounded task to a specialist and wait for their deliverable. Maximum eight tasks per goal.", {"employee_id":{"type":"string", "enum":[k for k in self.roles if k != "director"]}, "title":{"type":"string"}, "instructions":{"type":"string"}}))
                scout.save_json(folder / "agent.json", config)
        self.persist()
        self.operations = Operations(self)

    def employee_folder(self, identifier):
        if identifier not in self.roles:
            raise scout.ScoutError("Unknown employee.")
        return self.folder / "team" / identifier

    def remote_state(self, identifier):
        path = self.employee_folder(identifier) / "state.json"
        return scout.load_json(path) if path.exists() else {}

    def persist(self):
        scout.save_json(self.path, self.store)

    def changed(self):
        with self.condition:
            self.persist()
            self.sequence += 1
            self.events.append({"id":self.sequence, "kind":"refresh"})
            self.events = self.events[-1000:]
            self.condition.notify_all()

    def log(self, message, task=None):
        with self.lock:
            self.store["activity"].append({"time":now(), "message":scout.redact(message), "task_id":task["id"] if task else None, "employee_id":task["employee_id"] if task else None})
            self.store["activity"] = self.store["activity"][-500:]
            self.changed()

    def snapshot(self):
        with self.lock:
            employees = []
            for e in self.employees:
                state = self.remote_state(e["id"])
                employees.append({**e, "configuration":scout.load_json(self.employee_folder(e["id"]) / "agent.json"), "agent_id":state.get("agent_id"), "session_id":state.get("session_id"), "turn_status":state.get("turn_status"), "busy":any(t["employee_id"] == e["id"] and t["id"] in self.active for t in self.store["tasks"])})
            ready=self.os.can_run() if hasattr(self,'os') else bool(os.environ.get('OPENAI_API_KEY','').strip())
            return copy.deepcopy({"app":"crewbot-workspace","token":self.token,"sequence":self.sequence,"credential_ready":ready,"project_id":scout.PROJECT,"company":scout.load_json(self.folder / "company.json"),"employees":employees, **self.store, **self.operations.snapshot()})

    def task(self, identifier):
        for task in self.store["tasks"]:
            if task["id"] == identifier:
                return task
        raise scout.ScoutError("Unknown task.")

    def create_task(self, body, parent_id=None, start=True):
        employee = str(body.get("employee_id", "director"))
        self.employee_folder(employee)
        title = str(body.get("title", "")).strip()[:200]
        text = str(body.get("description", "")).strip()
        if not title or not text or len(text) > 100000:
            raise scout.ScoutError("Add a title and a task brief under 100,000 characters.")
        scout.message_input(title + text)
        priority = body.get("priority", "normal")
        if priority not in ("normal", "high", "low"):
            raise scout.ScoutError("Invalid priority.")
        with self.lock:
            if hasattr(self,'os') and sum(t['created_at'][:10]==now()[:10] for t in self.store['tasks'])>=self.os.settings['daily_task_limit']:
                raise scout.ScoutError('Daily task limit reached. Review work or adjust the limit in Settings.')
            task = {"id":uuid.uuid4().hex, "title":title,"description":text,"employee_id":employee,"priority":priority,"parent_id":parent_id,"created_at":now(),"status":"queued","result":"","error":None,"artifacts":[],"messages":[{"role":"user","text":text}],"delegations":0}
            self.store["tasks"].append(task)
            self.log("Task assigned: " + title, task)
        if start:
            self.launch(task["id"])
        return task

    def launch(self, identifier, resume=False):
        with self.lock:
            task = self.task(identifier)
            if identifier in self.active or task["status"] in ("review", "done", "cancelled"):
                raise scout.ScoutError("This task is already running or finished.")
            ready=self.os.can_run(task['employee_id']) if hasattr(self,'os') else bool(os.environ.get('OPENAI_API_KEY','').strip())
            if not ready:
                task.update(status="blocked",error="Add your selected provider API key in Settings, then run this task.")
                self.changed()
                return None
            self.active.add(identifier)
            if hasattr(self,'os') and not task.get('submitted'):task['provider']=self.os.provider(task['employee_id'])
            task.update(status="queued",error=None)
            self.changed()
        thread = threading.Thread(target=self.worker, args=(identifier,resume), daemon=True)
        thread.start()
        return thread

    def delegate(self, parent, args):
        employee = args.get("employee_id")
        if self.roles[parent['employee_id']].get('capability_role',parent['employee_id']) != "director" or employee == parent['employee_id'] or employee not in self.roles:
            raise scout.ScoutError("The Director can delegate only to a specialist.")
        with self.lock:
            if parent["delegations"] >= 8 or parent["status"] != "running":
                raise scout.ScoutError("Delegation limit reached or parent task stopped.")
            # Reuse a matching child after interrupted tool execution; do not duplicate work.
            child = next((t for t in self.store["tasks"] if t["parent_id"] == parent["id"] and t["employee_id"] == employee and t["description"] == args.get("instructions")), None)
            if child is None:
                parent["delegations"] += 1
                child = self.create_task({"employee_id":employee,"title":args.get("title"),"description":args.get("instructions")}, parent["id"])
        deadline = time.monotonic() + 850
        with self.condition:
            while child["status"] in ("queued", "running") and parent["status"] == "running" and time.monotonic() < deadline:
                self.condition.wait(timeout=5)
        return {"task_id":child["id"], "employee_id":employee,"status":child["status"],"result":child["result"][:60000],"error":child["error"],"artifacts": [{"name":a["name"]} for a in child["artifacts"]]}

    def worker(self, identifier, resume):
        task = self.task(identifier)
        if hasattr(self,'os') and task.get('provider')=='openrouter':
            return self.os.run_task(task)
        employee = task["employee_id"]
        try:
            with self.employee_locks[employee]:
                with self.lock:
                    if task["status"] == "cancelled":
                        return
                    task["status"] = "running"
                    task["messages"] = [{"role":"user","text":task["description"]},{"role":"assistant","text":""}]
                    self.changed()
                def output(text):
                    with self.lock:
                        task["messages"][-1]["text"] += scout.redact(text)
                        self.changed()
                def event(value):
                    kind = value.get("type", "unknown")
                    turn = value.get("turn") or {}
                    if kind == "agent.session.turn.created" and turn.get("subagent_id") is None:
                        with self.lock:
                            task["turn_id"] = value.get("turn_id") or turn.get("id")
                            self.changed()
                    if not kind.endswith(".delta"):
                        self.log(kind, task)
                handlers = {"get_company_context":lambda _:scout.load_json(self.folder / "company.json"), "list_company_tasks":lambda _: [{k:t[k] for k in ("id","title","employee_id","status","result")} for t in self.snapshot()["tasks"][-50:]]}
                if employee == "director":
                    handlers["delegate_task"] = lambda args:self.delegate(task,args)
                handlers.update(self.operations.handlers(task))
                if hasattr(self,'os'):handlers.update(self.os.handlers(task))
                api = self.api_factory()
                if not (resume and task.get('submitted')):
                    self.prepare_employee(api,employee)
                app = scout.Scout(api,self.employee_folder(employee),output=output,notify=lambda text:self.log(text,task),on_event=event,tool_handlers=handlers)
                task.setdefault("previous_turn_id",app.state.get("turn_id"))
                text = "Shared company knowledge:\n" + json.dumps(scout.load_json(self.folder / "company.json"),ensure_ascii=False) + "\n\nCompany workspace:\n" + json.dumps(self.operations.context(),ensure_ascii=False) + "\n\nAssigned task: " + task["title"] + "\n" + task["description"]
                if resume and app.state.get("session_id") and task.get("submitted"):
                    if not task.get("turn_id") and app.state.get("turn_id") == task.get("previous_turn_id") and not app.state.get("pending_previous_turn"):
                        task["submitted"] = False
                        raise scout.ScoutError("The previous attempt did not begin a new turn. Run this task again.")
                    app.watch()
                elif app.state.get("session_id"):
                    if app.state.get("turn_status") not in (None,"completed","failed","cancelled"):
                        raise scout.ScoutError("Previous turn may still be working. Resume that task before submitting new work.")
                    with self.lock:
                        task["submitted"] = True
                        self.changed()
                    app.send(text)
                else:
                    with self.lock:
                        task["submitted"] = True
                        self.changed()
                    app.start("First verify the hosted runtime by running Python and writing then reading /workspace/runtime-check.txt. Then complete this task:\n" + text)
                result = (app.run_dir / "output.txt").read_text(encoding="utf-8")
                with self.lock:
                    task.update(result=result,run_directory=str(app.run_dir),session_id=app.state.get("session_id"),turn_id=app.state.get("turn_id"))
                    task["messages"][-1]["text"] = result
                    self.changed()
                try:
                    for artifact in api.pages(app.session_path("/artifacts")):
                        if artifact.get("turn_id") != app.state.get("turn_id"):
                            continue
                        artifact_id = artifact["id"]
                        if not re.fullmatch(r"[A-Za-z0-9_-]+",artifact_id):
                            continue
                        name = Path(artifact.get("path") or artifact.get("filename") or artifact_id).name
                        name = re.sub(r'[^\w. -]', '_',name)[:180] or artifact_id
                        destination = app.run_dir / "artifacts" / (artifact_id + "-" + name)
                        api.download(app.session_path("/artifacts/" + artifact_id + "/content"),destination)
                        with self.lock:
                            task["artifacts"].append({"id":artifact_id,"name":name,"local_path":str(destination)})
                except Exception as exc:
                    self.log("Artifact retrieval: " + scout.redact(exc),task)
                with self.lock:
                    if task["status"] != "cancelled":
                        task.update(status="review",completed_at=now())
                    self.changed()
                self.log("Deliverable ready for review",task)
        except Exception as exc:
            with self.lock:
                if task["status"] != "cancelled":
                    saved = self.remote_state(employee)
                    if not task.get("turn_id") and saved.get("turn_id") == task.get("previous_turn_id") and not saved.get("pending_previous_turn") and not saved.get("session_creation_pending"):
                        task["submitted"] = False
                    task.update(status="paused" if task.get("submitted") and saved.get("session_id") else "blocked",error=scout.redact(exc))
                self.log("Task needs attention: " + scout.redact(exc),task)
        finally:
            with self.lock:
                self.active.discard(identifier)
                if hasattr(self,'os'):self.os.finished(task)
                self.operations.sync_flow(task)
                self.changed()

    def prepare_employee(self,api,identifier):
        """Apply new tool definitions without altering an interrupted turn's session."""
        state=self.remote_state(identifier)
        definition=scout.load_json(self.employee_folder(identifier)/'agent.json')
        if state.get('agent_id') and state.get('saved_agent_definition') not in (None,definition):
            if state.get('turn_status') not in (None,'completed','cancelled','failed') or state.get('pending_previous_turn'):
                raise scout.ScoutError('Resume the previous turn before upgrading this employee.')
            api.request('POST','/agents/'+state['agent_id'],definition)
            state['saved_agent_definition']=definition
            if state.get('session_id'):
                state.setdefault('previous_sessions',[]).append(state['session_id'])
            for key in ('session_id','turn_id','turn_status'):
                state.pop(key,None)
            scout.save_json(self.employee_folder(identifier)/'state.json',state)
            self.log('Employee tools upgraded; a fresh session will use the new definition.')

    def action(self, body):
        identifier, action = body.get("task_id"), body.get("action")
        task = self.task(identifier)
        if action in ("run","resume"):
            # A submitted turn must be observed, not resent.
            return self.launch(identifier,resume=bool(task.get("submitted")))
        if action == "complete":
            with self.lock:
                if task["status"] != "review":
                    raise scout.ScoutError("Only reviewed deliverables can be completed.")
                task["status"] = "done"
                self.changed()
                self.operations.sync_flow(task)
            return
        if action == "cancel":
            with self.lock:
                if task["status"] in ("review","done"):
                    raise scout.ScoutError("This task is already finished.")
                was_running = task["status"] == "running"
                task["status"] = "cancelled"
                self.changed()
            state = self.remote_state(task["employee_id"])
            self.operations.sync_flow(task)
            if was_running and task.get('provider')!='openrouter' and state.get("session_id"):
                self.api_factory().request("POST",f"/agents/sessions/{state['session_id']}/events",{"events":[{"type":"agent.session.input.cancel"}]})
            return
        raise scout.ScoutError("Unknown task action.")

    def company(self, body):
        existing = scout.load_json(self.folder / "company.json")
        for key in existing:
            value = body.get(key,existing[key])
            if not isinstance(value,type(existing[key])):
                raise scout.ScoutError("Invalid company field: " + key)
            if isinstance(value,list) and any(not isinstance(x,str) for x in value):
                raise scout.ScoutError("Company lists must contain text.")
            existing[key] = value
        scout.message_input(json.dumps(existing))
        with self.lock:
            scout.save_json(self.folder / "company.json",existing)
            self.log("Company knowledge updated. Every new task receives this context.")

    def configure(self, body):
        identifier = body.get("employee_id")
        folder = self.employee_folder(identifier)
        if not self.employee_locks[identifier].acquire(blocking=False):
            raise scout.ScoutError("Wait until this employee finishes working.")
        try:
            with self.lock:
                if any(self.task(i)["employee_id"] == identifier for i in self.active):
                    raise scout.ScoutError("This employee has queued work.")
            config = scout.load_json(folder / "agent.json")
            for key in ("name","model","instructions"):
                value = str(body.get(key,config[key])).strip()
                if not value or len(value) > 100000:
                    raise scout.ScoutError("Invalid " + key)
                scout.message_input(value)
                config[key] = value
            state = self.remote_state(identifier)
            if state.get("agent_id"):
                self.api_factory().request("POST","/agents/" + state["agent_id"],config)
                state["saved_agent_definition"] = config
                scout.save_json(folder / "state.json",state)
            scout.save_json(folder / "agent.json",config)
            self.log("Employee configuration saved. Start a fresh conversation to apply instructions.")
        finally:
            self.employee_locks[identifier].release()

    def reset_session(self, identifier):
        folder = self.employee_folder(identifier)
        with self.lock:
            if any(self.task(i)["employee_id"] == identifier for i in self.active):
                raise scout.ScoutError("Wait for this employee's queued work to finish.")
            state = self.remote_state(identifier)
            if state.get("session_id"):
                state.setdefault("previous_sessions",[]).append(state["session_id"])
            for key in ("session_id","turn_id","turn_status","pending_input","session_creation_pending"):
                state.pop(key,None)
            scout.save_json(folder / "state.json",state)
            self.log("Fresh conversation selected for " + self.roles[identifier]["name"] + ". Previous work is retained.")

PAGE_CSP="sandbox allow-scripts allow-forms allow-popups allow-modals; default-src 'none'; img-src data: https:; media-src data: https:; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src data: https://fonts.gstatic.com; script-src 'unsafe-inline'; connect-src 'none'; form-action 'none'; frame-ancestors 'self'; base-uri 'none'"
ASSET_CSP="sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"

def make_handler(workspace):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def trusted(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}")
        def reply(self,value,status=200,mime="application/json; charset=utf-8",filename=None,csp=None):
            raw = value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type",mime)
            self.send_header("Content-Length",str(len(raw)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Content-Security-Policy",csp or "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; media-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if filename:
                self.send_header("Content-Disposition","attachment; filename*=UTF-8''" + quote(filename))
            self.end_headers()
            self.wfile.write(raw)
        def stream(self,handle,size,mime,filename=None,csp=None,cacheable=False):
            """Send a file-like object in chunks, honoring a single HTTP byte Range (video seeking, Safari)."""
            start,end,status=0,size-1,200
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',self.headers.get('Range','').strip())
            if match and size and (match[1] or match[2]):
                if match[1]:start=int(match[1]);end=min(int(match[2]),size-1) if match[2] else size-1
                else:start=max(0,size-int(match[2]))
                if start>end or start>=size:
                    self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
                status=206
            self.send_response(status)
            self.send_header("Content-Type",mime);self.send_header("Content-Length",str(end-start+1));self.send_header("Accept-Ranges","bytes")
            if status==206:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
            # Asset files are content-addressed by a random ID and never change after creation.
            self.send_header("Cache-Control","private, max-age=31536000, immutable" if cacheable else "no-store")
            self.send_header("X-Content-Type-Options","nosniff");self.send_header("Content-Security-Policy",csp or ASSET_CSP)
            if filename:self.send_header("Content-Disposition","attachment; filename*=UTF-8''" + quote(filename))
            self.end_headers()
            handle.seek(start);remaining=end-start+1
            try:
                while remaining>0:
                    chunk=handle.read(min(1048576,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def do_GET(self):
            if not self.trusted():
                return self.reply({"error":"Untrusted host"},403)
            parsed = urlparse(self.path)
            if parsed.path == '/api/os/state' and hasattr(workspace,'hub'):
                try:return self.reply(workspace.hub.snapshot(parse_qs(parsed.query).get('company',['default'])[0]))
                except scout.ScoutError as exc:return self.reply({'error':scout.redact(exc)},404)
            if parsed.path == '/api/os/file' and hasattr(workspace,'hub'):
                try:
                    query=parse_qs(parsed.query);tenant=workspace.hub.get(query.get('company',['default'])[0])
                    if query.get('report'):
                        report=next(r for r in tenant.store['reports'] if r['id']==query['report'][0])
                        return self.reply(report['content'].encode(),mime='text/plain; charset=utf-8',filename='operating-report.txt')
                    task=tenant.task(query.get('task',[''])[0])
                    if not query.get('artifact'):return self.reply(task['result'].encode(),mime='text/plain; charset=utf-8',filename='task-result.txt')
                    artifact=next(a for a in task['artifacts'] if a['id']==query['artifact'][0]);path=Path(artifact['local_path']).resolve()
                    if not path.is_relative_to(tenant.folder.resolve()/'team'):raise ValueError('Invalid artifact')
                    return self.reply(path.read_bytes(),mime='application/octet-stream',filename=artifact['name'])
                except (ValueError,scout.ScoutError,StopIteration,OSError):return self.reply({'error':'File not found in this company'},404)
            if parsed.path == '/api/os/logo' and hasattr(workspace,'hub'):
                try:
                    tenant=workspace.hub.get(parse_qs(parsed.query).get('company',['default'])[0])
                    path=tenant.os.logo_path()
                    if not path or not path.is_file():raise ValueError('No logo')
                    return self.reply(path.read_bytes(),mime=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                except (ValueError,scout.ScoutError,OSError):return self.reply({'error':'Company logo not found'},404)
            if parsed.path in ('/api/os/asset','/api/os/asset-export') and hasattr(workspace,'hub'):
                try:
                    query=parse_qs(parsed.query);tenant=workspace.hub.get(query.get('company',['default'])[0])
                    if parsed.path=='/api/os/asset-export':
                        with tenant.os.media.export_zip() as archive:
                            size=archive.seek(0,2);return self.stream(archive,size,'application/zip',filename='brand-assets.zip')
                    item=tenant.os.media.asset(query.get('id',[''])[0]);download=query.get('download',[''])[0]=='1'
                    name=re.sub(r'[^A-Za-z0-9._ -]+','-',item.get('title','asset'))[:80]+'.'+item['filename'].rsplit('.',1)[1]
                    if item['kind']=='page':
                        # Generated HTML is untrusted: opaque-origin sandbox, no network, only inline styles/scripts and inlined assets.
                        return self.reply(tenant.os.media.render_page(item),mime='text/html; charset=utf-8',filename=name if download else None,csp=PAGE_CSP)
                    path=tenant.os.media.asset_path(item)
                    with open(path,'rb') as handle:return self.stream(handle,path.stat().st_size,item['mime'],filename=name if download else None,cacheable=True)
                except (ValueError,scout.ScoutError,OSError):return self.reply({'error':'Asset not found in this company'},404)
            if parsed.path == "/api/state":
                return self.reply(workspace.snapshot())
            if parsed.path in ('/api/events','/api/os/events'):
                try:
                    event_workspace=workspace
                    if parsed.path=='/api/os/events':event_workspace=workspace.hub.get(parse_qs(parsed.query).get('company',['default'])[0])
                    cursor = int(self.headers.get("Last-Event-ID") or parse_qs(parsed.query).get("after",["0"])[0])
                except (ValueError,scout.ScoutError,AttributeError):
                    return self.reply({"error":"Invalid cursor"},400)
                self.send_response(200)
                self.send_header("Content-Type","text/event-stream")
                self.send_header("Cache-Control","no-cache")
                self.end_headers()
                try:
                    while True:
                        with event_workspace.condition:
                            pending = [e for e in event_workspace.events if e["id"] > cursor]
                            if not pending:
                                event_workspace.condition.wait(timeout=15)
                                pending = [e for e in event_workspace.events if e["id"] > cursor]
                        for event in pending:
                            self.wfile.write(f"id: {event['id']}\ndata: {json.dumps(event)}\n\n".encode())
                            cursor = event["id"]
                        if not pending:
                            self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                    pass
                return
            if parsed.path in ("/api/result","/api/artifact"):
                try:
                    query = parse_qs(parsed.query)
                    task = workspace.task(query.get("task",[""])[0])
                    if parsed.path == "/api/result":
                        return self.reply(task["result"].encode(),mime="text/plain; charset=utf-8",filename=task["title"][:100]+".txt")
                    artifact = next(a for a in task["artifacts"] if a["id"] == query.get("id",[""])[0])
                    path = Path(artifact["local_path"]).resolve()
                    if not path.is_relative_to(workspace.folder.resolve() / "team"):
                        raise ValueError("Invalid artifact")
                    return self.reply(path.read_bytes(),mime="application/octet-stream",filename=artifact["name"])
                except (scout.ScoutError,ValueError,StopIteration,OSError):
                    return self.reply({"error":"Deliverable not found"},404)
            if parsed.path == '/api/record-export':
                try:
                    query=parse_qs(parsed.query)
                    collection=query.get('collection',[''])[0]
                    if collection not in ('pages','offers','projects','leads'): raise ValueError('Invalid collection')
                    record=workspace.operations.find(collection,query.get('id',[''])[0])
                    if collection in ('pages','offers'):
                        return self.reply(record['content'].encode(),mime='text/plain; charset=utf-8',filename=record['title']+'.md')
                    return self.reply(json.dumps(record,ensure_ascii=False,indent=2).encode(),mime='application/json',filename=collection+'.json')
                except (ValueError,scout.ScoutError):
                    return self.reply({'error':'Record not found'},404)
            assets = {"/":"landing.html" if hasattr(workspace,'hub') else "index.html","/app":"os.html","/landing.css":"landing.css","/landing.js":"landing.js","/legacy":"index.html","/os.js":"os.js","/os.css":"os.css","/app.js":"app.js","/styles.css":"styles.css","/operations.js":"operations.js","/operations.css":"operations.css","/studio.js":"studio.js","/studio.css":"studio.css","/brand/icon.svg":"brand/icon.svg","/brand/logo.svg":"brand/logo.svg","/favicon.ico":"brand/icon.svg"}
            if re.fullmatch(r'/bots/[a-z][a-z0-9-]{0,45}\.svg',parsed.path):assets[parsed.path]=parsed.path[1:]
            if parsed.path in assets:
                path = ROOT / "web" / assets[parsed.path]
                if not path.is_file():return self.reply({'error':'Asset not found'},404)
                return self.reply(path.read_bytes(),mime=mimetypes.guess_type(path)[0] + "; charset=utf-8")
            return self.reply({"error":"Not found"},404)
        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.trusted() or (origin and origin not in (f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}")) or not secrets.compare_digest(self.headers.get("X-Scout-Token",""),workspace.token):
                return self.reply({"error":"Refresh the local workspace before trying again."},403)
            try:
                length = int(self.headers.get("Content-Length","0"))
                if length < 0 or length > 3000000:
                    return self.reply({"error":"Request too large"},413)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body,dict):
                    raise ValueError("Expected an object")
                result = {"ok":True}
                if self.path == '/api/os/action' and hasattr(workspace,'hub'):
                    result.update(workspace.hub.action(body))
                elif self.path == "/api/tasks":
                    result["task_id"] = workspace.create_task(body)["id"]
                elif self.path == "/api/task-action":
                    workspace.action(body)
                elif self.path == "/api/company":
                    workspace.company(body)
                elif self.path == "/api/configuration":
                    workspace.configure(body)
                elif self.path == "/api/reset-session":
                    workspace.reset_session(body.get("employee_id"))
                elif self.path == '/api/flows':
                    result['flow']=workspace.operations.create_flow(body)
                elif self.path == '/api/flow-action':
                    workspace.operations.flow_action(body)
                elif self.path == '/api/projects':
                    result['project']=workspace.operations.create_project(body)
                elif self.path == '/api/milestones':
                    workspace.operations.milestone(body)
                elif self.path == '/api/milestone-action':
                    result['task']=workspace.operations.milestone_action(body)
                elif self.path == '/api/record-action':
                    workspace.operations.record_action(body)
                elif self.path == '/api/pages':
                    result['page']=workspace.operations.save_page(body)
                elif self.path == '/api/connections':
                    workspace.operations.configure_connection(body)
                elif self.path == '/api/connection-check':
                    result['receipt']=workspace.operations.check_connection(body.get('connection'))
                elif self.path == '/api/propose-action':
                    result['approval']=workspace.operations.propose(body)
                elif self.path == '/api/approval-action':
                    workspace.operations.approval_action(body)
                elif self.path == '/api/schedules':
                    result['schedule']=workspace.operations.save_schedule(body)
                elif self.path == "/api/shutdown":
                    with workspace.lock:
                        members=list(workspace.hub.companies.values()) if hasattr(workspace,'hub') else [workspace]
                        if any(w.active or any(a['status']=='executing' for a in w.store.get('approvals',[])) or any(r['delivery']=='sending' for r in w.store.get('reports',[])) for w in members):
                            raise scout.ScoutError("Finish or stop active work before restarting the server.")
                    self.reply({"ok":True},202)
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                else:
                    return self.reply({"error":"Not found"},404)
                self.reply(result,202)
            except (ValueError,scout.ScoutError,TypeError) as exc:
                self.reply({"error":scout.redact(exc)},400)
    return Handler

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--open",action="store_true")
    args = parser.parse_args()
    data_folder=ROOT/'.crewbot'/'default'
    data_folder.mkdir(parents=True,exist_ok=True)
    for name in ('company.json','employees.json','agent.json'):
        destination=data_folder/name
        if not destination.exists():
            import shutil
            shutil.copy(ROOT/name,destination)
    workspace=Workspace(data_folder)
    from company_os import OSHub
    hub=OSHub(workspace)
    threading.Thread(target=hub.scheduler,daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1",args.port),make_handler(workspace))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}"
    print("CrewBot: " + url + " | Dashboard: " + url + "/app",flush=True)
    print("API key: " + ("configured" if os.environ.get("OPENAI_API_KEY") else "missing; local setup available"),flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Workspace stopped. Remote turns may continue; resume after restarting.")
    finally:
        workspace.operations.stop.set()
        hub.stop.set()
        server.server_close()
if __name__ == "__main__":
    main()
