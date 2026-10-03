"""E2E harness: real CrewBot server + scripted localhost OpenRouter (documented response shapes). No real keys."""
import os, struct, sys, tempfile, threading, zlib, shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
for v in ('OPENAI_API_KEY','OPENROUTER_API_KEY','TAVILY_API_KEY','FIRECRAWL_API_KEY','RESEND_API_KEY','SLACK_BOT_TOKEN','GITHUB_TOKEN'): os.environ[v] = ''
from http.server import ThreadingHTTPServer
import company_dashboard as company
from company_os import OSHub
from providers import ProviderHTTP
import test_studio

def gradient_png(w, h, a, b):
    rows = b''.join(b'\x00' + b''.join(bytes([int(a[i] + (b[i]-a[i]) * ((x + y) / (w + h))) for i in range(3)]) for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')

folder = Path(tempfile.mkdtemp(prefix='crewbot-e2e-'))
for name in ('company.json', 'employees.json', 'agent.json'): shutil.copy(company.ROOT / name, folder / name)
fixture = test_studio.OpenRouterFixture().__enter__()
fixture.image = gradient_png(256, 256, (231, 110, 86), (29, 52, 64))
workspace = company.Workspace(folder)
hub = OSHub(workspace)
app = workspace.os

def respond(body):
    messages = body['messages']; system = messages[0]['content']; last = messages[-1]
    if last['role'] == 'tool':
        return []  # final text reply
    user = last['content'] if isinstance(last['content'], str) else ''
    if 'CrewBot Mentor' in system:
        if 'Fixture Bakery' in user:
            return [('update_business_profile', {'name': 'Fixture Bakery', 'summary': 'Artisan bread and office catering', 'market': 'Offices in Austin, TX', 'goals': 'Launch a catering brand and get 20 office clients'})]
        return []
    if 'senior full-stack software engineer' in system:
        images = [a for a in workspace.store['assets'] if a['kind'] == 'image']
        img = ('<img alt="Brand" style="width:120px" src="{{asset:' + images[-1]['id'] + '}}">') if images else ''
        page = '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:system-ui;margin:0;background:#fff8f3;color:#1d3440}header{padding:48px 24px;text-align:center}h1{font-size:40px;margin:16px 0}a.cta{display:inline-block;background:#e76e56;color:#fff;padding:14px 22px;border-radius:10px;text-decoration:none}</style></head><body><header>' + img + '<h1>Fresh bread for your whole office</h1><p>Daily catering for Austin teams.</p><a class="cta" href="#order">Book a tasting</a></header><script>fetch("/api/os/state").then(()=>document.title="LEAKED").catch(()=>document.body.dataset.sandbox="blocked")</script></body></html>'
        return [('build_web_page', {'title': 'Catering landing page', 'html': page, 'page_type': 'optin', 'funnel': 'Office catering', 'step': 1, 'notes': 'E2E'})]
    if 'brand and visual designer' in system:
        return [('generate_image', {'prompt': 'Launch post with a croissant', 'purpose': 'social_post', 'title': 'Launch post'})]
    return []
fixture.responder = respond

def connect(w):
    w.os.vault.set('openrouter', 'local-openrouter-e2e-key')
    w.os.http = ProviderHTTP(w.os.vault, fixture.url); w.operations.http = w.os.http
connect(workspace)
original_get = hub.get
def get(identifier):
    w = original_get(identifier)
    if not isinstance(w.os.http, ProviderHTTP) or w.os.http.local_origin != fixture.url: connect(w)
    return w
hub.get = get
threading.Thread(target=hub.scheduler, daemon=True).start()
# Fast video polling for the E2E run.
import media; media.POLL_SECONDS = 2
server = ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 8790), company.make_handler(workspace))
server.daemon_threads = True
print('READY', f'http://127.0.0.1:{server.server_port}', folder, flush=True)
server.serve_forever()
