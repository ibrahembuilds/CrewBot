#!/usr/bin/env python3
"""Agents HTTP API CLI. All HTTP traffic is performed by curl, without an SDK."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode, urlparse
import uuid

ROOT = Path(__file__).resolve().parent
PROJECT = os.environ.get("OPENAI_PROJECT_ID", "")
BASE_URL = "https://api.openai.com/v1"
# Add application-owned, bounded functions here if you add function tools to agent.json.
# Hosted web_search and shell calls are executed by OpenAI, not by this registry.
TOOL_HANDLERS = {}
SECRETS = set()


class ScoutError(RuntimeError):
    pass


def redact(value):
    text = str(value)
    for key in tuple(SECRETS):
        if key: text=text.replace(key,'[REDACTED]')
    for name in ("OPENAI_API_KEY", "GITHUB_TOKEN", "SLACK_BOT_TOKEN", "OPENROUTER_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY", "RESEND_API_KEY"):
        key = os.environ.get(name, "")
        if key:
            text = text.replace(key, "[REDACTED]")
    return re.sub(r"(?<![A-Za-z0-9_-])(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-|tvly-|fc-|re_)[A-Za-z0-9_-]{8,}", "[REDACTED]", text)


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(redact(json.dumps(value, ensure_ascii=False, indent=2)), encoding="utf-8")
        # Windows readers and antivirus can briefly hold a file without delete sharing.
        for attempt in range(8):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.025 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def message_input(text):
    known_keys=list(SECRETS)+[os.environ.get(name,'') for name in ('OPENAI_API_KEY','GITHUB_TOKEN','SLACK_BOT_TOKEN','OPENROUTER_API_KEY','TAVILY_API_KEY','FIRECRAWL_API_KEY','RESEND_API_KEY')]
    if re.search(r"(?<![A-Za-z0-9_-])(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-|tvly-|fc-|re_)[A-Za-z0-9_-]{8,}", text) or any(len(key)>=8 and key in text for key in known_keys):
        raise ScoutError("Message appears to contain an API key. Remove credentials from the message.")
    return [{"role": "user", "content": [{"type": "input_text", "text": text}]}]


def curl_quote(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def api_error(status, body):
    hints = {
        401: "Check OPENAI_API_KEY; the key must be valid for this project.",
        403: "Check project access and api.agents.read, api.agents.write, api.responses.write permissions.",
        404: "Check resource IDs, model availability, and Agents API access for the project.",
        409: "The session may be busy. Inspect status; cancel or wait before cleanup.",
        429: "Check quota, billing, and rate limits. Inspect saved work before retrying input.",
    }
    return ScoutError(redact(f"HTTP {status}: {body[:3000]}\n{hints.get(status, 'Inspect the request and saved state before retrying.')}"))


class CurlStream:
    def __init__(self, process, headers, timeout):
        self.process, self.headers, self.timeout = process, headers, timeout
        self.lines = queue.Queue()
        self.stderr = ""
        self.deadline = time.monotonic() + timeout
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line.rstrip("\r\n"))
        finally:
            self.lines.put(None)

    def _read_stderr(self):
        self.stderr = self.process.stderr.read()

    def wait_ready(self):
        # curl writes response headers before body data. No sleep-based send race.
        while time.monotonic() < self.deadline:
            raw = self.headers.read_bytes() if self.headers.exists() else b""
            complete = re.findall(rb"HTTP/[^\r\n]+\r?\n.*?\r?\n\r?\n", raw, re.S)
            if complete:
                final = complete[-1]
                status = int(final.splitlines()[0].split()[1])
                if status >= 400:
                    self.process.wait(timeout=max(1, self.deadline - time.monotonic()))
                    body = []
                    while True:
                        line = self.lines.get(timeout=2)
                        if line is None:
                            break
                        body.append(line)
                    raise api_error(status, "\n".join(body))
                # Ignore proxy CONNECT and informational response headers.
                if b"content-type: text/event-stream" in final.lower():
                    return
            if self.process.poll() is not None:
                raise ScoutError(redact(f"curl did not open an SSE stream (exit {self.process.returncode}). {self.stderr}"))
            time.sleep(0.05)
        raise ScoutError("Timed out opening the event stream.")

    def events(self,cancelled=None):
        data, event_name = [], None
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ScoutError("Observation timed out. Work may still be running; use watch or status.")
            try:
                if cancelled and cancelled():raise ScoutError('Conversation stopped.')
                line = self.lines.get(timeout=min(remaining,.5) if cancelled else remaining)
            except queue.Empty as exc:
                if cancelled:continue
                raise ScoutError("Observation timed out. Work may still be running; use watch or status.") from exc
            if line is None or line == "":
                if data:
                    raw = "\n".join(data)
                    data = []
                    if raw == "[DONE]":
                        return
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ScoutError("Malformed JSON in the SSE stream.") from exc
                    if not isinstance(event, dict):
                        raise ScoutError("Expected an object in the SSE stream.")
                    event.setdefault("type", event_name or "unknown")
                    yield event
                event_name = None
                if line is None:
                    code = self.process.wait(timeout=5)
                    if code:
                        raise ScoutError(redact(f"curl stream failed (exit {code}): {self.stderr}"))
                    return
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            # SSE comments, heartbeat lines, id, and retry fields are not payloads.

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe and not pipe.closed:
                pipe.close()


class CurlAPI:
    def __init__(self, project=None, base_url=BASE_URL, timeout=900, key=None):
        self.key = (key if key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()
        if not self.key:
            raise ScoutError("OPENAI_API_KEY is not set. Run run.ps1 to enter it securely in your local terminal.")
        parsed = urlparse(base_url)
        if base_url.rstrip("/") != BASE_URL and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise ScoutError("Only the OpenAI API or a local test server is allowed as the API origin.")
        project = project if project is not None else (os.environ.get("OPENAI_PROJECT_ID") or PROJECT)
        if base_url.rstrip("/") == BASE_URL and not project:
            raise ScoutError("Set OPENAI_PROJECT_ID for the CLI, or set your company project in dashboard Settings.")
        if project and not re.fullmatch(r"proj_[A-Za-z0-9_-]+",project):raise ScoutError("Invalid OpenAI project ID.")
        self.base_url, self.project, self.timeout = base_url.rstrip("/"), project, timeout
        self.curl = shutil.which("curl.exe") or shutil.which("curl")
        if not self.curl:
            raise ScoutError("curl is required. On Windows use curl.exe; Python 3.10+ is also required.")

    @contextlib.contextmanager
    def launch(self, method, path, body=None, stream=False, submission_key=None):
        if not path.startswith("/") or "\n" in path or "\r" in path:
            raise ScoutError("Invalid API path.")
        with tempfile.TemporaryDirectory(prefix="scout-curl-") as temporary:
            folder = Path(temporary)
            headers = folder / "headers.txt"
            args = [self.curl, "-q", "--silent", "--show-error", "--fail-with-body",
                    "--no-buffer", "--connect-timeout", "15", "--max-time",
                    str(self.timeout if stream else 360), "--request", method,
                    "--dump-header", str(headers), "--config", "-", self.base_url + path]
            if body is not None:
                payload = folder / "request.json"
                payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
                args += ["--data-binary", "@" + str(payload)]
            if not stream:
                args += ["--write-out", "\nSCOUT_HTTP_STATUS:%{http_code}"]
            # Authorization passes through stdin, never command arguments or files.
            config_headers = ["Authorization: Bearer " + self.key,
                              "OpenAI-Project: " + self.project, "OpenAI-Beta: agents=v1",
                              "Content-Type: application/json",
                              "Accept: " + ("text/event-stream" if stream else "application/json")]
            if submission_key:
                config_headers.append("Idempotency-Key: " + submission_key)
            config = "\n".join("header = " + curl_quote(h) for h in config_headers) + "\n"
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            observer = None
            try:
                if stream:
                    process.stdin.write(config)
                    process.stdin.close()
                    observer = CurlStream(process, headers, self.timeout)
                    observer.wait_ready()
                    yield observer
                else:
                    output, stderr = process.communicate(config, timeout=370)
                    body_text, marker, code = output.rpartition("\nSCOUT_HTTP_STATUS:")
                    status = int(code) if marker and code.isdigit() else 0
                    if status >= 400:
                        raise api_error(status, body_text)
                    if process.returncode or not status:
                        raise ScoutError(redact(f"curl transport failure (exit {process.returncode}): {stderr}"))
                    if not body_text.strip():
                        yield {}
                    else:
                        try:
                            yield json.loads(body_text)
                        except json.JSONDecodeError as exc:
                            raise ScoutError("The API returned a non-JSON response.") from exc
            finally:
                if observer:
                    observer.close()
                else:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe and not pipe.closed:
                            pipe.close()

    def request(self, method, path, body=None, submission_key=None):
        with self.launch(method, path, body, submission_key=submission_key) as result:
            return result

    def download(self, path, destination):
        """Download an artifact directly with curl, keeping credentials off disk."""
        if not path.startswith("/agents/sessions/") or "\n" in path or "\r" in path:
            raise ScoutError("Invalid artifact path.")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        headers = ["Authorization: Bearer " + self.key, "OpenAI-Project: " + self.project, "OpenAI-Beta: agents=v1"]
        config = "\n".join("header = " + curl_quote(value) for value in headers) + "\n"
        args = [self.curl, "-q", "--silent", "--show-error", "--fail-with-body", "--connect-timeout", "15",
                "--max-time", "180", "--config", "-", "--output", str(temporary),
                "--write-out", "%{http_code}", self.base_url + path]
        try:
            result = subprocess.run(args, input=config, text=True, encoding="utf-8", capture_output=True, timeout=190)
            status = int(result.stdout) if result.stdout.isdigit() else 0
            if status >= 400:
                raise api_error(status, temporary.read_text(encoding="utf-8", errors="replace")[:3000])
            if result.returncode or status != 200:
                raise ScoutError(redact(f"Artifact download failed: HTTP {status}; {result.stderr}"))
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def pages(self, path, **params):
        params.update(order="asc", limit=100)
        while True:
            page = self.request("GET", path + "?" + urlencode(params))
            yield from page.get("data", [])
            if not page.get("has_more"):
                break
            cursor = page.get("last_id")
            if not cursor or cursor == params.get("after"):
                raise ScoutError("Invalid pagination cursor in API response.")
            params["after"] = cursor


def validate_leads(text):
    try:
        leads = json.loads(text)
    except json.JSONDecodeError:
        return {"kind": "text", "note": "May be a company-intake question; no lead array was returned."}
    keys = ["Lead Name", "Type of Lead", "Reasoning and Evaluation", "Conclusion & Recommendation"]
    errors = []
    if not isinstance(leads, list):
        return {"kind": "json", "valid": False, "errors": ["Expected a JSON array."]}
    for index, lead in enumerate(leads):
        if not isinstance(lead, dict) or list(lead) != keys or any(not isinstance(lead[k], str) or not lead[k].strip() for k in keys):
            errors.append(f"Entry {index + 1}: expected the four specified nonempty string fields in order.")
    return {"kind": "leads", "valid": not errors, "lead_count": len(leads),
            "default_minimum_met": len(leads) >= 5, "errors": errors,
            "note": "Structure checks do not verify sources, facts, or business suitability."}


class Scout:
    def __init__(self, api, folder=ROOT, output=None, notify=None, on_event=None, tool_handlers=None):
        self.api, self.folder = api, Path(folder)
        self.output_sink, self.notify_sink, self.event_sink = output, notify, on_event
        self.tool_handlers = TOOL_HANDLERS if tool_handlers is None else tool_handlers
        self.state_path = self.folder / "state.json"
        self.state = load_json(self.state_path) if self.state_path.exists() else {"project_id": api.project}
        if self.state.get("project_id") != api.project:
            raise ScoutError("Saved state belongs to a different project.")
        self.parts = {}
        self.run_dir = None

    def save(self):
        save_json(self.state_path, self.state)

    def note(self, text):
        if self.notify_sink:
            self.notify_sink(redact(text))
        else:
            print(redact(text), file=sys.stderr, flush=True)

    def output(self, text="", end="\n"):
        if self.output_sink:
            self.output_sink(redact(text) + end)
        else:
            print(redact(text), end=end, flush=True)

    def session_path(self, suffix=""):
        identifier = self.state.get("session_id")
        if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ScoutError("No valid saved session ID. Use run to create a session.")
        return "/agents/sessions/" + identifier + suffix

    def prepare_log(self):
        self.parts = {}
        self.run_dir = self.folder / "runs" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        self.run_dir.mkdir(parents=True)
        self.state["last_run_directory"] = str(self.run_dir)
        self.save()

    def handle_actions(self, session):
        for action in session.get("required_actions", []):
            kind = action.get("type")
            if kind != "function_call":
                raise ScoutError(f"Pending {kind} requires additional configuration. Hosted runtime connection is managed by OpenAI; inspect status for an unexpected connection request.")
            turn_id, call_id = action["turn_id"], action["call_id"]
            ledger_key = f"{self.state['session_id']}:{turn_id}:{call_id}"
            ledger = self.state.setdefault("tool_results", {})
            if ledger_key not in ledger:
                result = {"type": "agent.session.input.tool_result", "turn_id": turn_id, "call_id": call_id}
                try:
                    handler = self.tool_handlers.get(action["name"])
                    if handler is None:
                        raise ScoutError("No registered application handler for " + action["name"])
                    arguments = action["arguments"]
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    result.update(success=True, output=redact(json.dumps(handler(arguments), ensure_ascii=False)))
                except Exception as exc:
                    result.update(success=False, error=redact(str(exc)))
                ledger[ledger_key] = {"event": result, "submission_key": str(uuid.uuid4())}
                self.save()  # Save outcome before submitting it, to avoid repeated execution.
            entry = ledger[ledger_key]
            self.api.request("POST", self.session_path("/events"), {"events": [entry["event"]]}, entry["submission_key"])
            self.note("Submitted function result for " + action["name"])

    def handle_event(self, event):
        if self.event_sink:
            self.event_sink(json.loads(redact(json.dumps(event))))
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as log:
            log.write(redact(json.dumps(event, ensure_ascii=False)) + "\n")
        kind = event.get("type", "unknown")
        session = event.get("session") or {}
        if session.get("id"):
            self.state.update(session_id=session["id"], session_creation_pending=False)
        elif event.get("session_id"):
            self.state["session_id"] = event["session_id"]
        turn = event.get("turn") or {}
        root_turn = turn.get("subagent_id") is None
        if root_turn and event.get("turn_id"):
            self.state["turn_id"] = event["turn_id"]
        elif root_turn and turn.get("id"):
            self.state["turn_id"] = turn["id"]
        if kind == "agent.session.turn.created" and root_turn:
            self.state.pop("pending_previous_turn", None)
        self.save()
        if kind == "agent.session.turn.output_text.delta":
            key = (event.get("item_id"), event.get("content_index", 0))
            self.parts[key] = self.parts.get(key, "") + event["delta"]
            self.output(event["delta"], end="")
        elif kind == "agent.session.turn.output_text.done":
            key = (event.get("item_id"), event.get("content_index", 0))
            if key not in self.parts:
                self.output(event["text"], end="")
            self.parts[key] = event["text"]
        else:
            self.note("[event] " + kind)
        if kind == "agent.session.requires_action":
            self.handle_actions(self.api.request("GET", self.session_path()))
        if kind in ("error", "agent.session.failed", "agent.session.environment.failed"):
            raise ScoutError(redact(json.dumps(event)))
        if root_turn and kind in ("agent.session.turn.failed", "agent.session.turn.cancelled"):
            self.state["turn_status"] = kind.rsplit(".", 1)[1]
            self.save()
            raise ScoutError(redact(f"Turn {self.state.get('turn_id')} {self.state['turn_status']}: {turn.get('error')}"))
        if root_turn and kind == "agent.session.turn.completed":
            self.state["turn_status"] = "completed"
            self.save()
            return True
        return False

    def consume(self, observer):
        for event in observer.events():
            if self.handle_event(event):
                self.output()
                return
        raise ScoutError("Stream closed before the intended turn completed. Use watch to reconcile saved work; do not resend the initial message.")

    def finish(self):
        turn_id = self.state.get("turn_id")
        if not turn_id:
            raise ScoutError("No turn ID received; retrieve session status before retrying.")
        turn = self.api.request("GET", self.session_path("/turns/" + turn_id))
        save_json(self.run_dir / "turn.json", turn)
        self.state["turn_status"] = turn.get("status")
        self.save()
        if turn.get("status") != "completed":
            raise ScoutError(redact(f"Turn {turn_id} status: {turn.get('status')}; error: {turn.get('error')}"))
        items = list(self.api.pages(self.session_path("/items"), turn_id=turn_id))
        save_json(self.run_dir / "items.json", items)
        messages = [item for item in items if item.get("type") == "message" and item.get("role") == "assistant"]
        final_messages = [item for item in messages if item.get("phase") == "final_answer"]
        chosen = final_messages or messages[-1:]
        text = "\n".join(part.get("text", "") for item in chosen for part in item.get("content", []) if part.get("type") == "output_text")
        if not text:
            text = "\n".join(self.parts.values())
        if not text:
            raise ScoutError("Completed turn has no assistant text. Inspect saved items and tool outcomes.")
        (self.run_dir / "output.txt").write_text(redact(text), encoding="utf-8")
        validation = validate_leads(text)
        save_json(self.run_dir / "validation.json", validation)
        if validation.get("kind") == "leads" and validation.get("valid"):
            save_json(self.run_dir / "leads.json", json.loads(text))
        if not self.parts:
            self.output(text)
        self.note(f"Completed turn {turn_id}. Saved output and events in {self.run_dir}")
        self.note("Output check: " + json.dumps(validation))

    def start(self, text, new_session=False):
        if self.state.get("session_id") and not new_session:
            self.note("Reusing session " + self.state["session_id"])
            self.watch()
            return
        if self.state.get("session_creation_pending") and not new_session:
            raise ScoutError("Previous session creation had an uncertain outcome. Inspect project sessions and adopt the ID with --session-id, or explicitly use --new-session.")
        agent = load_json(self.folder / "agent.json")
        if not self.state.get("agent_id"):
            if self.state.get("agent_creation_pending"):
                raise ScoutError("Previous agent creation had an uncertain outcome. Inspect project agents and adopt the returned ID with --agent-id.")
            self.state["agent_creation_pending"] = True
            self.save()
            try:
                saved = self.api.request("POST", "/agents", agent)
            except ScoutError as exc:
                # Definite 4xx rejection did not create an agent.
                if str(exc).startswith("HTTP 4"):
                    self.state["agent_creation_pending"] = False
                    self.save()
                raise
            self.state.update(agent_id=saved["id"], agent_creation_pending=False, saved_agent_definition=agent)
            self.save()
            self.note("Created reusable agent " + saved["id"])
        elif self.state.get("saved_agent_definition") not in (None, agent):
            raise ScoutError("agent.json changed since the saved agent was created. Create or update the reusable agent explicitly, then adopt its ID with --agent-id.")
        if new_session and self.state.get("session_id"):
            self.state.setdefault("previous_sessions", []).append(self.state["session_id"])
            self.note("Retaining previous session " + self.state["session_id"])
        self.state.pop("session_id", None)
        self.state.pop("turn_id", None)
        self.state["turn_status"] = None
        body = {"agent_id": self.state["agent_id"], "environment": {"type": "openai_hosted", "container_size": "small"},
                "input": message_input(text), "stream": True}
        self.prepare_log()
        self.state["session_creation_pending"] = True
        self.save()
        try:
            with self.api.launch("POST", "/agents/sessions", body, stream=True) as observer:
                self.consume(observer)
        except ScoutError as exc:
            if str(exc).startswith("HTTP 4") and not self.state.get("session_id"):
                self.state["session_creation_pending"] = False
                self.save()
            raise
        self.finish()

    def watch(self):
        self.prepare_log()
        # Subscribe first, then fetch snapshots while the reader buffers live events.
        with self.api.launch("GET", self.session_path("/events?stream=true"), stream=True) as observer:
            session = self.api.request("GET", self.session_path())
            save_json(self.run_dir / "session.json", session)
            if session.get("status") == "failed":
                raise ScoutError(redact(f"Session failed: {session.get('error')}"))
            if not self.state.get("turn_id") or "pending_previous_turn" in self.state:
                turns = list(self.api.pages(self.session_path("/turns")))
                roots = [turn for turn in turns if turn.get("subagent_id") is None
                         and turn.get("id") != self.state.get("pending_previous_turn")]
                if roots:
                    self.state["turn_id"] = roots[-1]["id"]
                    self.state.pop("pending_previous_turn", None)
                    self.save()
            # Reconcile persisted messages instead of reassembling missed deltas.
            if self.state.get("turn_id"):
                turn = self.api.request("GET", self.session_path("/turns/" + self.state["turn_id"]))
                if turn.get("status") == "completed":
                    self.finish()
                    return
                if turn.get("status") in ("failed", "cancelled"):
                    raise ScoutError(redact(f"Turn {turn['id']} {turn['status']}: {turn.get('error')}"))
            self.handle_actions(session)
            for event in observer.events():
                previous = self.state.get("pending_previous_turn")
                if previous and (event.get("turn_id") or (event.get("turn") or {}).get("id")) == previous:
                    continue
                if self.handle_event(event):
                    self.output()
                    break
            else:
                raise ScoutError("Stream closed before the intended turn completed. Use watch again; do not resend input.")
        self.finish()

    def send(self, text):
        session = self.api.request("GET", self.session_path())
        if session.get("status") != "idle":
            raise ScoutError("Use watch or cancel first: this CLI sends follow-ups only to idle sessions.")
        self.prepare_log()
        event = {"type": "agent.session.input.message", "input": message_input(text)}
        submission = {"events": [event]}
        submission_key = str(uuid.uuid4())
        save_json(self.run_dir / "submission.json", {"body": submission, "idempotency_key": submission_key})
        previous_turn = self.state.get("turn_id")
        with self.api.launch("GET", self.session_path("/events?stream=true"), stream=True) as observer:
            self.state.update(turn_id=None, turn_status=None, pending_previous_turn=previous_turn)
            self.save()
            self.api.request("POST", self.session_path("/events"), submission, submission_key)
            # Ignore an old terminal event buffered before input was submitted.
            for event in observer.events():
                if previous_turn and (event.get("turn_id") or (event.get("turn") or {}).get("id")) == previous_turn:
                    continue
                if self.handle_event(event):
                    self.output()
                    break
            else:
                raise ScoutError("Follow-up stream closed early. Use watch; the submission key is saved for recovery.")
        self.finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=900, help="Maximum observation duration in seconds")
    parser.add_argument("--agent-id", help="Adopt an existing reusable agent ID")
    parser.add_argument("--session-id", help="Adopt an existing session ID")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Create an agent and session, or resume the saved session")
    run.add_argument("--message-file", type=Path, default=ROOT / "initial-message.txt")
    run.add_argument("--new-session", action="store_true")
    run.add_argument("--once", action="store_true", help="Exit after the initial or resumed turn")
    send = commands.add_parser("send", help="Send and stream a follow-up message")
    send_group = send.add_mutually_exclusive_group(required=True)
    send_group.add_argument("--message")
    send_group.add_argument("--message-file", type=Path)
    commands.add_parser("watch", help="Reconnect and reconcile the saved turn without resending input")
    commands.add_parser("status", help="Retrieve session, required actions, and saved turn")
    commands.add_parser("cancel", help="Cancel active work while keeping the session")
    commands.add_parser("delete-session", help="Delete saved session and request hosted sandbox cleanup")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    app = None
    try:
        api = CurlAPI(timeout=args.timeout)
        app = Scout(api)
        for argument, field in ((args.agent_id, "agent_id"), (args.session_id, "session_id")):
            if argument:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", argument):
                    raise ScoutError("Invalid resource ID.")
                app.state[field] = argument
                app.state[field.replace("_id", "_creation_pending")] = False
                if field == "session_id":
                    app.state.pop("turn_id", None)
                else:
                    app.state.pop("saved_agent_definition", None)
                app.save()
        if args.command == "run":
            app.start(args.message_file.read_text(encoding="utf-8-sig"), args.new_session)
            if not args.once and sys.stdin.isatty():
                app.note("Enter company details or follow-ups. /quit keeps the session; /cancel cancels; /delete deletes it.")
                while True:
                    text = input("\nYou> ").strip()
                    if text == "/quit":
                        break
                    if text == "/cancel":
                        api.request("POST", app.session_path("/events"), {"events": [{"type": "agent.session.input.cancel"}]})
                    elif text == "/delete":
                        api.request("DELETE", app.session_path())
                        app.state.pop("session_id", None)
                        app.state.pop("turn_id", None)
                        app.save()
                        break
                    elif text:
                        app.send(text)
        elif args.command == "send":
            text = args.message if args.message is not None else args.message_file.read_text(encoding="utf-8-sig")
            app.send(text)
        elif args.command == "watch":
            app.watch()
        elif args.command == "status":
            status = api.request("GET", app.session_path())
            print(redact(json.dumps(status, ensure_ascii=False, indent=2)))
            if app.state.get("turn_id"):
                print(redact(json.dumps(api.request("GET", app.session_path("/turns/" + app.state["turn_id"])), indent=2)))
        elif args.command == "cancel":
            api.request("POST", app.session_path("/events"), {"events": [{"type": "agent.session.input.cancel"}]})
            app.note("Cancellation submitted. Use status to confirm the turn outcome.")
        elif args.command == "delete-session":
            api.request("DELETE", app.session_path())
            app.state.pop("session_id", None)
            app.state.pop("turn_id", None)
            app.save()
            app.note("Session deleted; hosted sandbox cleanup is asynchronous. Reusable agent is retained.")
        return 0
    except (ScoutError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print("Error: " + redact(exc), file=sys.stderr)
        if app:
            print("Saved IDs: " + json.dumps({k: app.state.get(k) for k in ("agent_id", "session_id", "turn_id")}), file=sys.stderr)
            print("Use status/watch before resending input. Closing a stream does not stop remote work.", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("\nObserver closed; remote work may continue. Use watch, status, or cancel.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
