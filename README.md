# CrewBot

A company workspace where Mentor learns your business, proposes AI employees, and turns goals into tracked work. Employees can now produce finished brand deliverables: logos, posters, social graphics, short videos, landing pages and funnels, through the OpenRouter image, video and chat models you choose. Starts blank: one Mentor, no preset business, specialist employees or demo leads.

## Run

Requires Python 3.11+ and curl 7.76+ (with --fail-with-body). Uses the Python standard library: no npm install, pip install or Docker. Node is needed only for syntax checks.

```powershell
git clone git@github.com:ibrahembuilds/CrewBot.git
cd CrewBot
powershell -ExecutionPolicy Bypass -File .\dashboard.ps1
```

Or on Windows, Linux or macOS:

```sh
python company_dashboard.py --port 8765
```

Open **http://127.0.0.1:8765/** for the landing page and **http://127.0.0.1:8765/app** for the workspace. Keep the terminal running. `dashboard.ps1 -Port 8767 -NoBrowser` uses another port.

## Set up your business

1. Settings → API keys: add OpenRouter. The default chat model is `typesafe/jev-router`; you can enter another supported OpenRouter chat model ID. Availability and charges depend on your account.
2. Chat with Mentor about your business name, offering, buyers/region, goals and existing tools. Answers are saved incrementally. **Set up your business** is an optional form shortcut.
3. Ask Mentor to propose a team tailored to your business. Review the saved roles and connections in **AI employees**, then **Approve and create crew**. Advice alone does not create employees.
4. Chat directly with employees or ask Mentor to assign work. Each assignment has an owner, brief, status, output and linked follow-ups. Edit names, responsibilities, capabilities and models in the role editor, or ask Mentor to change a role.
5. Connect research/collaboration tools and create staffed workflows, project milestones and recurring responsibilities.

**Add a company** creates separate employees, keys, knowledge, tasks, reports and branding. No owner's business profile or project is shipped.

## Studio: brand assets, video, landing pages and funnels

Studio is the company's creative department. It uses only your OpenRouter key.

| Deliverable | How it is made | Notes |
|---|---|---|
| Logo, social post (4:5), story (9:16), poster (2:3), banner (16:9), ad, product shot | `POST /api/v1/images` with the image model you pick from OpenRouter's live catalog | Purpose presets set the aspect ratio. Vector models (e.g. Recraft) return SVG. Up to 4 per request, optional reference image. |
| Short brand video | Asynchronous `POST /api/v1/videos`, then the scheduler polls and downloads the file | Duration, ratio and resolution come from the selected model. Optional start frame from a Studio image. Billed only when complete. |
| Landing page, website page, multi-step funnel | An engineer, designer or marketer employee writes standalone HTML with `build_web_page` | Brand images are embedded with `{{asset:ID}}` placeholders and inlined on preview/export. Each version is kept. |

- **Expert roster.** AI employees → *Hire an expert* adds a ready-made role in one click: software engineer (websites/funnels), brand designer (logos/posters/video), marketing lead (campaigns/social), researcher, offer strategist, operations director, automation, customer success, architect. Each role's capability bounds its tools: only designer and marketing roles can render video, and only engineer/designer/marketing roles can build pages.
- **Brand kit.** Settings → Company branding adds *visual style* and *brand voice*. Together with the business name, offering, audience and colors, they are applied to every image/video prompt and every employee's context. A generated raster logo can become the workspace logo.
- **Pick any model.** Set company defaults in Settings (image, video, chat) and override the chat model per employee with any tool-capable OpenRouter model ID.
- **Real costs and a budget.** Every chat round, image and video records the provider-reported `usage.cost`. Studio shows this month's spend by type. Generation and new employee work stop at the monthly budget (Settings, default $10). Failed generations record no spend. Identical tool calls within a task reuse the saved result instead of paying twice.
- **Safe preview and export.** Generated pages and assets are served with a sandbox Content-Security-Policy: opaque origin, no network access, no access to the workspace or its action token. Download any file, or *Export all* as a zip with a manifest.
- **Brand launch kit workflow.** Designer (logo, hero, launch post) → engineer (landing page using those assets) → marketer (launch plan and posts).

Studio pages are HTML previews: forms do not submit until you connect your own backend, and CrewBot does not host or deploy pages.

## Make it yours

Settings → **Company branding** offers a workspace name, accent/navigation/background colors, live preview, save and discard. Upload your PNG, JPEG or WebP logo, up to 2 MB. Branding is saved per company. CrewBot includes an SVG logo and distinct employee icons.

## Providers and tools

| Provider | Configuration | Use |
|---|---|---|
| OpenRouter | OPENROUTER_API_KEY or Settings | Streamed chat and bounded function tools; Image API and Video API for Studio |
| OpenAI Agents | OPENAI_API_KEY and company project ID | Hosted sessions, web search and execution |
| Jev | OpenRouter key, typesafe/jev-1.13 | Recommend employee ownership through Decisions API |
| Tavily | TAVILY_API_KEY | Search with source URLs |
| Firecrawl | FIRECRAWL_API_KEY | Read public pages as Markdown |
| Slack | SLACK_BOT_TOKEN and allowed channel IDs | Scoped reads and reviewed messages |
| GitHub | GITHUB_TOKEN and allowed owner/repo values | Scoped issue reads and reviewed issue creation |
| Resend | RESEND_API_KEY, verified sender, owner recipient | Operating reports by email |
| Project management | Built in | Leads, offers, milestones, pages and handoffs |

Enter credentials in Settings without editing files. Only the default workspace reads process environment variables; added companies need their own keys. `.env.example` lists names only; the app does **not** automatically load .env files.

Session-only keys stay in server memory. On Windows, choosing **Remember securely** encrypts keys with your Windows account's DPAPI; other operating systems support session-only storage. Values are never returned in state/reports. Removing a credential disables environment fallback for that company until re-entered. See [CONNECTIONS.md](CONNECTIONS.md).

## Work and automation

Workflows resolve current employees by capability, including custom IDs; missing roles make a workflow unavailable. Research → offer → delivery and scope → architecture → implementation → automation → handoff retain prior-stage outputs. Milestones keep owners and acceptance criteria. Internal handoffs normally wait for review; the owner can enable automatic handoffs with a daily task cap.

Autopilot creates recurring responsibilities when enabled. Slack messages/GitHub issues require individual review before external writes. Reports can be saved locally or sent to the configured owner email; automatic reports require an explicit setting.

Schedules need this server and computer running. This is a local single-owner application without public authentication, multi-user access control or a cloud worker. Companies are separate workspaces within that owner's application, not authenticated tenants. See [IMPROVEMENTS.md](IMPROVEMENTS.md).

## OpenAI Agents: direct curl client

The hosted dashboard provider and `scout.py` call the Agents HTTP API **directly through curl**, without an Agents SDK. `agent.json` defines the reusable Business Lead and Partnership Scout: five researched leads, evaluation before recommendation, source evidence, live web search, gpt-6.1-sol, medium reasoning. Intake asks for missing details; researched results use the requested four-field JSON array.

Supply OPENAI_API_KEY and OPENAI_PROJECT_ID in your terminal environment, then:

```powershell
python scout.py run --once
python scout.py send --message "Research five partners using the company brief I provided."
python scout.py status
python scout.py watch
python scout.py --help
```

`run.ps1` optionally prompts for OPENAI_API_KEY with hidden input. OPENAI_PROJECT_ID is required for live CLI requests; the dashboard uses its company-specific project setting. No owner's project ID is hardcoded. Your key needs agent/session permissions and access to the chosen model.

The client creates a reusable definition with POST /v1/agents, then uses its returned agent_id in POST /v1/agents/sessions. The selected openai_hosted small environment provisions a runtime and executor through OpenAI. SSE displays output/events, handles required function results and distinguishes completion, failure and cancellation. Follow-up streams open before input is posted. Uncertain creations and saved idempotency keys prevent automatic replay.

OpenAI hosted sessions support execution. OpenRouter employees save source/text and use configured application tools; they do not execute generated code. Describing browser actions, deployments or external writes does not perform them.

Python launches curl with argument lists (shell=False), temporary JSON files and credential headers via stdin. Temporary files are removed. No automatic mutation retries. See the [Agents API overview](https://developers.openai.com/api/docs/guides/agents-api/overview).

## Storage and recovery

Private dashboard data lives in ignored `.crewbot/default/`; additional companies are beneath that folder. Source company.json and employees.json remain blank. The standalone scout uses ignored state.json and runs/ in its working directory. Back up runtime data separately from Git. Keep keys out of employee chats.

A disconnected hosted turn may continue remotely. Use Resume or scout.py watch; closing a terminal is not remote cancellation. Missing-key/provider/tool errors appear as blocked work. Uncertain report/external dispatches require receipt inspection before retrying.

## Tests

```sh
python -m unittest discover -v
node --check web/os.js
node --check web/landing.js
node --check web/app.js
node --check web/operations.js
node --check web/studio.js
```

Tests use isolated localhost providers and actual curl, with no live credentials. CI runs on Ubuntu and Windows. See [VERIFICATION.md](VERIFICATION.md), [COMPANY-OS-GUIDE.md](COMPANY-OS-GUIDE.md) and [BRAND.md](BRAND.md).
