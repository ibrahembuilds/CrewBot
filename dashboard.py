"""Local dashboard for Business Lead and Partnership Scout (stdlib only)."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse, parse_qs
import webbrowser

import scout

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
BRIEF_FIELDS = ("company", "website", "offering", "region", "goals", "lead_types", "constraints")


class Dashboard:
    def __init__(self, folder=ROOT, api_factory=scout.CurlAPI):
        self.folder, self.api_factory = Path(folder), api_factory
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.token = secrets.token_urlsafe(32)
        self.events, self.sequence, self.busy = [], 0, False
        self.status, self.error = "Ready", None
        self.store_path = self.folder / "dashboard-state.json"
        self.store = scout.load_json(self.store_path) if self.store_path.exists() else {"brief": {}, "messages": [], "leads": [], "activity": []}

    def state_file(self):
        path = self.folder / "state.json"
        return scout.load_json(path) if path.exists() else {}

    def persist(self):
        scout.save_json(self.store_path, self.store)

    def emit(self, kind, data):
        with self.condition:
            self.sequence += 1
            self.events.append({"id": self.sequence, "kind": kind, "data": data})
            self.events = self.events[-1000:]
            self.condition.notify_all()

    def snapshot(self):
        with self.lock:
            state = self.state_file()
            session_id = state.get("session_id")
            sessions = list(dict.fromkeys(([session_id] if session_id else []) + state.get("previous_sessions", [])))
            return {
                "token": self.token, "sequence": self.sequence, "credential_ready": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
                "project_id": scout.PROJECT, "agent": scout.load_json(self.folder / "agent.json"),
                "agent_id": state.get("agent_id"), "session_id": session_id, "sessions": sessions,
                "turn_status": state.get("turn_status"), "busy": self.busy, "status": self.status, "error": self.error,
                "brief": self.store["brief"], "messages": self.store["messages"], "leads": self.store["leads"],
                "activity": self.store["activity"][-200:],
            }

    def brief(self, values):
        clean = {k: str(values.get(k, "")).strip()[:10000] for k in BRIEF_FIELDS}
        scout.message_input(json.dumps(clean))
        with self.lock:
            self.store["brief"] = clean
            self.persist()
        self.emit("refresh", {})

    def brief_message(self):
        labels = {"company": "Company", "website": "Website", "offering": "Industry and offering", "region": "Target region", "goals": "Goals and values", "lead_types": "Desired lead types", "constraints": "Constraints"}
        return "Company brief:\n" + "\n".join(f"{labels[k]}: {v}" for k, v in self.store["brief"].items() if v) + "\nResearch at least 5 suitable real leads using the saved agent's output format. Ask for any important missing information before researching."

    def configuration(self, body):
        config = scout.load_json(self.folder / "agent.json")
        if body.get("effort") not in ("low", "medium", "high", "xhigh", "max"):
            raise scout.ScoutError("Choose a supported reasoning effort.")
        if body.get("verbosity") not in ("low", "medium", "high"):
            raise scout.ScoutError("Choose a supported response detail level.")
        for field in ("name", "model", "instructions"):
            value = str(body.get(field, "")).strip()
            if not value or len(value) > (128 if field == "name" else 100000):
                raise scout.ScoutError(f"Invalid {field}.")
            scout.message_input(value)
            config[field] = value
        config["reasoning"]["effort"] = body["effort"]
        config["text"]["verbosity"] = body["verbosity"]
        return config

    def launch(self, action, body=None):
        body = body or {}
        if action not in ("start", "send", "watch", "cancel", "delete", "select", "settings"):
            raise scout.ScoutError("Unknown action.")
        if action == "send":
            text = str(body.get("message", "")).strip()
            if not text or len(text) > 100000:
                raise scout.ScoutError("Enter a message of fewer than 100,000 characters.")
            scout.message_input(text)
        if action == "settings":
            body = {"configuration": self.configuration(body)}
        with self.lock:
            # Cancel can run alongside an observer; every other mutation is serialized.
            if self.busy and action != "cancel":
                raise scout.ScoutError("The agent is working. Wait for completion or stop the turn.")
            if not os.environ.get("OPENAI_API_KEY", "").strip() and not (action == "settings" and not self.state_file().get("agent_id")):
                raise scout.ScoutError("Set OPENAI_API_KEY in the server's terminal, then restart the dashboard. Use dashboard.ps1 for a hidden key prompt.")
            if action != "cancel":
                self.busy = True
            self.status, self.error = "Saving settings" if action == "settings" else "Working", None
        self.emit("refresh", {})
        thread = threading.Thread(target=self.worker, args=(action, body), daemon=True)
        thread.start()
        return thread

    def worker(self, action, body):
        message_id = None
        try:
            state = self.state_file()
            if action == "settings":
                config = body["configuration"]
                if state.get("agent_id"):
                    api = self.api_factory()
                    api.request("POST", "/agents/" + state["agent_id"], config)
                    state["saved_agent_definition"] = config
                    scout.save_json(self.folder / "state.json", state)
                scout.save_json(self.folder / "agent.json", config)
                self.log("Agent settings saved. New sessions will use these settings.")
            else:
                api = self.api_factory()
                app = scout.Scout(api, self.folder, output=lambda text: None, notify=self.log, on_event=self.agent_event)
                if action in ("start", "send", "watch", "select"):
                    if action == "start":
                        text = self.brief_message() if self.store["brief"].get("company") else (self.folder / "initial-message.txt").read_text(encoding="utf-8")
                    elif action == "send":
                        text = body["message"].strip()
                    else:
                        text = None
                    if text:
                        self.add_message("user", text)
                    message_id = self.add_message("assistant", "", streaming=True)
                    if action == "start":
                        app.start(text, new_session=True)
                    elif action == "send":
                        app.send(text)
                    else:
                        if action == "select":
                            identifier = body.get("session_id")
                            known = self.snapshot()["sessions"]
                            if identifier not in known:
                                raise scout.ScoutError("Choose a saved session.")
                            previous = app.state.get("session_id")
                            app.state.setdefault("previous_sessions", []).append(previous) if previous else None
                            app.state["session_id"] = identifier
                            app.state.pop("turn_id", None)
                            app.save()
                        app.watch()
                    text = (app.run_dir / "output.txt").read_text(encoding="utf-8")
                    with self.lock:
                        message = next(m for m in self.store["messages"] if m["id"] == message_id)
                        message.update(text=text, streaming=False, session_id=app.state.get("session_id"))
                        validation = scout.validate_leads(text)
                        if validation.get("kind") == "leads" and validation.get("valid"):
                            self.store["leads"] = [{**lead, "session_id": app.state.get("session_id"), "turn_id": app.state.get("turn_id")} for lead in json.loads(text)]
                        self.persist()
                elif action == "cancel":
                    api.request("POST", app.session_path("/events"), {"events": [{"type": "agent.session.input.cancel"}]})
                    self.log("Stop requested. Waiting for the turn's outcome.")
                elif action == "delete":
                    api.request("DELETE", app.session_path())
                    removed = app.state.pop("session_id", None)
                    app.state.pop("turn_id", None)
                    app.state["previous_sessions"] = [s for s in app.state.get("previous_sessions", []) if s != removed]
                    app.save()
                    self.log("Session deleted. The reusable agent is retained.")
            with self.lock:
                self.status = "Ready"
        except Exception as exc:
            with self.lock:
                self.error, self.status = scout.redact(str(exc)), "Needs attention"
                if message_id:
                    message = next(m for m in self.store["messages"] if m["id"] == message_id)
                    message.update(streaming=False, failed=True)
                    self.persist()
            self.log(self.error, "error")
        finally:
            with self.lock:
                if action != "cancel":
                    self.busy = False
            self.emit("refresh", {})

    def add_message(self, role, text, streaming=False):
        with self.lock:
            identifier = secrets.token_hex(8)
            self.store["messages"].append({"id": identifier, "role": role, "text": text, "streaming": streaming, "session_id": self.state_file().get("session_id"), "time": int(time.time())})
            self.store["messages"] = self.store["messages"][-150:]
            self.persist()
        self.emit("refresh", {})
        return identifier

    def agent_event(self, event):
        kind = event.get("type", "")
        if kind.endswith("output_text.delta") or kind.endswith("output_text.done"):
            with self.lock:
                message = next((m for m in reversed(self.store["messages"]) if m.get("streaming")), None)
                if message:
                    if kind.endswith(".delta"):
                        message["text"] += event.get("delta", "")
                    else:
                        message["text"] = event.get("text", message["text"])
                    self.emit("text", {"id": message["id"], "text": message["text"]})
        elif "reasoning_summary" not in kind:
            self.log(kind.replace("agent.session.", "").replace(".", " · "))

    def log(self, text, level="info"):
        if text.startswith("[event]"):
            return  # Already handled by the event callback.
        with self.lock:
            self.store["activity"].append({"text": scout.redact(text)[:3000], "level": level, "time": int(time.time())})
            self.store["activity"] = self.store["activity"][-200:]
            self.persist()
        self.emit("activity", self.store["activity"][-1])


def make_handler(dashboard):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def trusted(self):
            host = self.headers.get("Host", "")
            return host in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def reply(self, value, status=200, content_type="application/json"):
            raw = json.dumps(value, ensure_ascii=False).encode() if content_type == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.trusted():
                return self.reply({"error": "Untrusted host"}, 403)
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                return self.reply(dashboard.snapshot())
            if parsed.path == "/api/events":
                try:
                    cursor = int(self.headers.get("Last-Event-ID") or parse_qs(parsed.query).get("after", ["0"])[0])
                except ValueError:
                    return self.reply({"error": "Invalid cursor"}, 400)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        with dashboard.condition:
                            pending = [e for e in dashboard.events if e["id"] > cursor]
                            if not pending:
                                dashboard.condition.wait(timeout=15)
                                pending = [e for e in dashboard.events if e["id"] > cursor]
                        if pending:
                            for event in pending:
                                self.wfile.write(f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                                cursor = event["id"]
                        else:
                            self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            assets = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css", "/favicon.ico": None}
            if parsed.path == "/api/export":
                keys = ("Lead Name", "Type of Lead", "Reasoning and Evaluation", "Conclusion & Recommendation")
                return self.reply([{key: lead[key] for key in keys} for lead in dashboard.snapshot()["leads"]])
            if parsed.path not in assets or assets[parsed.path] is None:
                return self.reply({"error": "Not found"}, 404)
            path = WEB / assets[parsed.path]
            mime = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[path.suffix]
            self.reply(path.read_bytes(), content_type=mime)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.trusted() or (origin and origin not in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")) or not secrets.compare_digest(self.headers.get("X-Scout-Token", ""), dashboard.token):
                return self.reply({"error": "Refresh the local dashboard before trying again."}, 403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 300000:
                    return self.reply({"error": "Request too large."}, 413)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("Expected an object")
                if self.path == "/api/brief":
                    dashboard.brief(body)
                elif self.path.startswith("/api/action/"):
                    dashboard.launch(self.path.rsplit("/", 1)[-1], body)
                else:
                    return self.reply({"error": "Not found"}, 404)
                self.reply({"ok": True}, 202)
            except (ValueError, scout.ScoutError) as exc:
                self.reply({"error": scout.redact(str(exc))}, 400)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the dashboard in the default browser")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Dashboard()))
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Business Scout dashboard: {url}", flush=True)
    print("API key: " + ("configured" if os.environ.get("OPENAI_API_KEY") else "not configured; the dashboard is available for setup"), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Dashboard stopped. Active remote turns may continue.")
    finally:
        server.server_close()


if __name__ == "__main__":
    from company_dashboard import main as company_main
    company_main()
