"""Offline contract tests using real curl against a local Agents HTTP fixture."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import queue
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

import scout


def lead_text(count=5):
    return json.dumps([{
        "Lead Name": f"Fixture lead {i + 1}",
        "Type of Lead": "collaborator",
        "Reasoning and Evaluation": "Fixture evidence and risks; not a real recommendation.",
        "Conclusion & Recommendation": "Fixture only.",
    } for i in range(count)])


class Fixture:
    def __init__(self, mode="success"):
        self.mode = mode
        self.requests = []
        self.subscribed = threading.Event()
        self.followup = threading.Event()
        self.tool_returned = threading.Event()
        self.stop = threading.Event()
        self.turn = "turn_first"
        self.pending = []
        self.text = lead_text()
        self.tool_name = "unknown_tool"
        self.tool_arguments = {}
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def body(self):
                size = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(size)) if size else None

            def record(self, body=None):
                fixture.requests.append((self.command, self.path, body, dict(self.headers)))

            def reply(self, body, status=200):
                content = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def open_sse(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b": subscribed\r\n\r\n")
                self.wfile.flush()
                self.close_connection = True

            def emit(self, event):
                # Multibyte Unicode, CRLF framing, event names, and comments.
                content = ("event: " + event["type"] + "\r\n" +
                           "data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n")
                self.wfile.write(content.encode("utf-8"))
                self.wfile.flush()

            def stream_turn(self, initial=False):
                if initial:
                    self.emit({"type": "agent.session.created", "session": {"id": "sess_fixture"}})
                turn = {"id": fixture.turn, "subagent_id": None, "status": "in_progress"}
                self.emit({"type": "agent.session.turn.created", "session_id": "sess_fixture", "turn_id": fixture.turn, "turn": turn})
                if fixture.mode == "tool":
                    fixture.pending = [{"type": "function_call", "turn_id": fixture.turn,
                                        "call_id": "call_fixture", "name": fixture.tool_name, "arguments": fixture.tool_arguments}]
                    self.emit({"type": "agent.session.requires_action", "session": {"id": "sess_fixture"}})
                    if not fixture.tool_returned.wait(60):
                        return
                if fixture.mode == "disconnect":
                    return
                if fixture.mode in ("failed", "cancelled"):
                    turn["status"] = fixture.mode
                    turn["error"] = {"message": "Fixture failure"}
                    self.emit({"type": "agent.session.turn." + fixture.mode, "turn_id": fixture.turn, "turn": turn})
                    return
                # Idle is not a success signal; subagent terminal events also must not stop observation.
                self.emit({"type": "agent.session.idle", "session": {"id": "sess_fixture"}})
                self.emit({"type": "agent.session.turn.completed", "turn_id": "turn_subagent", "turn": {"id": "turn_subagent", "subagent_id": "sub_fixture"}})
                self.emit({"type": "agent.session.turn.output_text.delta", "turn_id": fixture.turn,
                           "item_id": "msg_fixture", "content_index": 0, "delta": "temporary text café"})
                self.emit({"type": "agent.session.turn.output_text.done", "turn_id": fixture.turn,
                           "item_id": "msg_fixture", "content_index": 0, "text": fixture.text})
                turn["status"] = "completed"
                self.emit({"type": "agent.session.turn.completed", "turn_id": fixture.turn, "turn": turn})

            def do_POST(self):
                body = self.body()
                self.record(body)
                if self.path == "/v1/agents":
                    if fixture.mode == "unauthorized":
                        self.reply({"error": {"message": "Invalid application key"}}, 401)
                    else:
                        self.reply({"id": "agent_fixture"})
                elif self.path == "/v1/agents/sessions":
                    self.open_sse()
                    self.stream_turn(initial=True)
                elif self.path.endswith("/events"):
                    event = body["events"][0]
                    if event["type"] == "agent.session.input.tool_result":
                        fixture.pending = []
                        fixture.tool_returned.set()
                    elif event["type"] == "agent.session.input.message":
                        if not fixture.subscribed.is_set():
                            self.reply({"error": "Subscribe first"}, 409)
                            return
                        fixture.turn = "turn_second"
                        fixture.followup.set()
                    self.reply({})
                else:
                    self.reply({"error": "Not found"}, 404)

            def do_GET(self):
                self.record()
                parsed = urlparse(self.path)
                if parsed.path.endswith("/events"):
                    self.open_sse()
                    fixture.subscribed.set()
                    while not fixture.stop.is_set():
                        if fixture.followup.wait(0.05):
                            self.stream_turn()
                            break
                elif parsed.path == "/v1/agents/sessions/sess_fixture":
                    self.reply({"id": "sess_fixture", "status": "requires_action" if fixture.pending else "idle", "required_actions": fixture.pending})
                elif "/turns/" in parsed.path:
                    status = fixture.mode if fixture.mode in ("failed", "cancelled") else "completed"
                    self.reply({"id": parsed.path.rsplit("/", 1)[-1], "status": status, "subagent_id": None})
                elif parsed.path.endswith("/turns"):
                    self.reply({"data": [{"id": fixture.turn, "status": "completed", "subagent_id": None}], "has_more": False})
                elif parsed.path.endswith("/artifacts"):
                    self.reply({"data": [{"id":"artifact_fixture", "turn_id":fixture.turn, "path":"/workspace/outputs/fixture.txt"}], "has_more":False})
                elif parsed.path.endswith("/artifacts/artifact_fixture/content"):
                    raw = b"Fixture artifact; local test only.\n"
                    self.send_response(200)
                    self.send_header("Content-Length",str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                elif parsed.path.endswith("/items"):
                    params = parse_qs(parsed.query)
                    if "after" not in params:
                        self.reply({"data": [{"id": "item_search", "type": "web_search_call"}], "has_more": True, "last_id": "item_search"})
                    else:
                        self.reply({"data": [{"id": "msg_fixture", "turn_id": fixture.turn, "type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": fixture.text}]}], "has_more": False})
                else:
                    self.reply({"error": "Not found"}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ScoutTests(unittest.TestCase):
    def setUp(self):
        self.project_patch=patch.object(scout,"PROJECT","proj_fixture");self.project_patch.start()
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "local-fixture-credential"})
        self.env.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        shutil.copy(scout.ROOT / "agent.json", self.folder / "agent.json")
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.out = contextlib.redirect_stdout(self.stdout)
        self.err = contextlib.redirect_stderr(self.stderr)
        self.out.__enter__()
        self.err.__enter__()

    def tearDown(self):
        self.project_patch.stop()
        self.err.__exit__(None, None, None)
        self.out.__exit__(None, None, None)
        self.temporary.cleanup()
        self.env.stop()

    def app(self, fixture):
        return scout.Scout(scout.CurlAPI(base_url=fixture.url, timeout=10), self.folder)

    def test_create_saved_agent_then_stream_session(self):
        with Fixture() as fixture:
            app = self.app(fixture)
            app.start("Ask for my company details.")
            calls = fixture.requests
            agent_request = next(c for c in calls if c[1] == "/v1/agents")
            self.assertEqual(agent_request[2]["name"], "Business Lead and Partnership Scout")
            self.assertEqual(agent_request[2]["model"], "gpt-6.1-sol")
            session_request = next(c for c in calls if c[1] == "/v1/agents/sessions")
            self.assertEqual(session_request[2]["agent_id"], "agent_fixture")
            self.assertNotIn("agent", session_request[2])
            self.assertEqual(session_request[2]["environment"]["type"], "openai_hosted")
            for call in calls:
                self.assertEqual(call[3]["OpenAI-Project"], scout.PROJECT)
                self.assertEqual(call[3]["OpenAI-Beta"], "agents=v1")
            self.assertEqual(app.state["turn_id"], "turn_first")
            self.assertEqual(app.state["turn_status"], "completed")
            self.assertEqual(scout.load_json(app.run_dir / "leads.json"), json.loads(fixture.text))
            self.assertIn("café", self.stdout.getvalue())
            self.assertTrue((app.run_dir / "events.jsonl").exists())
            self.assertEqual(len(scout.load_json(app.run_dir / "items.json")), 2)

    def test_followup_subscribes_before_posting_input(self):
        with Fixture() as fixture:
            app = self.app(fixture)
            app.start("Initial intake")
            app.send("Here is the company brief.")
            self.assertEqual(app.state["turn_id"], "turn_second")
            followup = next(c for c in fixture.requests if c[0] == "POST" and c[1].endswith("/events"))
            self.assertIn("Idempotency-Key", followup[3])
            submission = scout.load_json(app.run_dir / "submission.json")
            self.assertEqual(submission["idempotency_key"], followup[3]["Idempotency-Key"])

    def test_unknown_function_returns_error_and_continues(self):
        with Fixture("tool") as fixture:
            app = self.app(fixture)
            app.start("Initial intake")
            result = next(c[2]["events"][0] for c in fixture.requests if c[0] == "POST" and c[1].endswith("/events"))
            self.assertFalse(result["success"])
            self.assertEqual(result["turn_id"], "turn_first")
            self.assertEqual(result["call_id"], "call_fixture")
            self.assertEqual(app.state["turn_status"], "completed")

    def test_registered_handler_runs_once_on_duplicate_action(self):
        with Fixture() as fixture:
            app = self.app(fixture)
            app.state["session_id"] = "sess_fixture"
            invocations = []
            action = {"type": "function_call", "turn_id": "turn_first", "call_id": "call_one", "name": "lookup", "arguments": '{"company":"Example"}'}
            def lookup(arguments):
                invocations.append(arguments)
                return {"found": True}
            with patch.dict(scout.TOOL_HANDLERS, {"lookup": lookup}):
                app.handle_actions({"required_actions": [action]})
                app.handle_actions({"required_actions": [action]})
            self.assertEqual(len(invocations), 1)
            results = [c for c in fixture.requests if c[0] == "POST"]
            self.assertEqual(results[0][3]["Idempotency-Key"], results[1][3]["Idempotency-Key"])
            self.assertTrue(results[0][2]["events"][0]["success"])

    def test_disconnect_then_watch_recovers_without_resending(self):
        with Fixture("disconnect") as fixture:
            app = self.app(fixture)
            with self.assertRaisesRegex(scout.ScoutError, "Stream closed"):
                app.start("Initial intake")
            app.watch()
            creations = [c for c in fixture.requests if c[0] == "POST" and c[1] == "/v1/agents/sessions"]
            self.assertEqual(len(creations), 1)
            self.assertTrue((app.run_dir / "leads.json").exists())

    def test_failed_and_cancelled_turns_are_errors(self):
        for status in ("failed", "cancelled"):
            with self.subTest(status=status), Fixture(status) as fixture:
                app = self.app(fixture)
                app.state.clear()
                app.state["project_id"] = scout.PROJECT
                with self.assertRaisesRegex(scout.ScoutError, status):
                    app.start("Initial intake", new_session=True)
                self.assertEqual(app.state["turn_status"], status)

    def test_http_401_reports_error_without_credential(self):
        with Fixture("unauthorized") as fixture:
            app = self.app(fixture)
            with self.assertRaisesRegex(scout.ScoutError, "HTTP 401"):
                app.start("Initial intake")
            self.assertFalse(app.state["agent_creation_pending"])
            self.assertNotIn("local-fixture-credential", self.stderr.getvalue())

    def test_missing_credential_fails_before_http(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(scout.ScoutError, "not set"):
                scout.CurlAPI()

    def test_secret_redaction_and_prompt_rejection(self):
        self.assertEqual(scout.redact("token local-fixture-credential"), "token [REDACTED]")
        with self.assertRaisesRegex(scout.ScoutError, "API key"):
            scout.message_input("sk-proj-fictional-fixture-key")
        scout.save_json(self.folder / "data.json", {"error": "local-fixture-credential"})
        self.assertNotIn("local-fixture-credential", (self.folder / "data.json").read_text())

    def test_token_redaction_preserves_uuid_idempotency_keys_and_bounded_names(self):
        identifier = 'b00d830d-8afc-4a1b-bad3-a03490e8c6df'
        self.assertEqual(scout.redact(identifier), identifier)
        scout.save_json(self.folder / 'submission.json', {'idempotency_key': identifier})
        self.assertEqual(scout.load_json(self.folder / 'submission.json')['idempotency_key'], identifier)
        self.assertEqual(scout.redact('employee-care_coordinator'), 'employee-care_coordinator')
        self.assertEqual(scout.redact('Credential fc-fixturecredential123456'), 'Credential [REDACTED]')
        self.assertEqual(scout.redact('Credential re_fixturecredential123456'), 'Credential [REDACTED]')
        self.assertEqual(scout.message_input('Review task ' + identifier)[0]['content'][0]['text'], 'Review task ' + identifier)

    def test_no_credential_in_curl_arguments(self):
        original = scout.subprocess.Popen
        commands = []
        def capture(args, **kwargs):
            commands.append(args)
            return original(args, **kwargs)
        with Fixture() as fixture, patch.object(scout.subprocess, "Popen", side_effect=capture):
            self.app(fixture).start("Initial intake")
        self.assertTrue(commands)
        self.assertNotIn("local-fixture-credential", json.dumps(commands))
        self.assertTrue(all("--config" in c and "-" in c for c in commands))

    def test_unsupported_required_action_is_explicit(self):
        with Fixture() as fixture:
            app = self.app(fixture)
            with self.assertRaisesRegex(scout.ScoutError, "environment_connection"):
                app.handle_actions({"required_actions": [{"type": "environment_connection"}]})

    def test_minimum_and_json_structure_checks(self):
        self.assertTrue(scout.validate_leads(lead_text())["valid"])
        self.assertFalse(scout.validate_leads(lead_text(2))["default_minimum_met"])
        self.assertFalse(scout.validate_leads('[{"Lead Name":"Only one field"}]')["valid"])
        self.assertEqual(scout.validate_leads("What company should I research?")["kind"], "text")

    def test_sse_multiline_and_comments(self):
        observer = scout.CurlStream.__new__(scout.CurlStream)
        observer.lines = queue.Queue()
        observer.deadline = scout.time.monotonic() + 5
        observer.stderr = ""
        for line in [": heartbeat", "event: fixture", 'data: {"value":', 'data: "café"}', "", "data: [DONE]", ""]:
            observer.lines.put(line)
        self.assertEqual(list(observer.events()), [{"value": "café", "type": "fixture"}])

    def test_api_origin_guard(self):
        with self.assertRaisesRegex(scout.ScoutError, "API origin"):
            scout.CurlAPI(base_url="https://untrusted.example/v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
