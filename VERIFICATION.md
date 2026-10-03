# CrewBot verification

Run python -m unittest discover -v and the four JavaScript syntax checks in README.md. Tests use isolated localhost HTTP providers through actual curl, with no live keys.

Coverage includes reusable Agents and returned IDs, hosted session requests, SSE completion/error/reconnect, paginated items, idempotent submissions, bounded tools, OpenRouter streaming and malformed tool batches, incremental business intake, reviewed team proposals, role capability boundaries, branding/logo persistence, company isolation, origin/CSRF guards, handoffs, caps, milestones, schedules, reports and connector scopes.

Distribution guards require blank business/employee templates, no owner's branding/project/key, empty environment examples and no tracked credentials/runtime data. CI runs Python 3.11 on Windows and Ubuntu plus Node syntax checks, with provider variables empty.

Live access, billing, model availability, hosted execution, real search results, Slack/GitHub permissions and Resend delivery require the owner's keys. Fixture success does not prove external access. The local worker requires a running server/computer and provides no public authentication or always-on cloud worker.

Final release test and browser results are recorded below after verification.

## Release checks — 2026-10-03

- 88 automated tests passed in the final full release run (126.394 seconds), without failures or skips.
- Python compilation and all four JavaScript syntax checks passed.
- After the browser-only logo preview/layout correction, all 14 affected feature and HTTP release checks passed again.
- Browser: separate landing/app routes, blank Mentor-only startup, streamed fixture profile → tailored proposal → owner approval, four custom employees, colors/name/logo saved through UI and retained after reload, and a new company with default branding and no inherited key/logo.
- Layout inspected at the actual in-app browser viewport, including a narrow 735-pixel workspace and a wider 1280-pixel workspace. No phone-device or complete accessibility certification is claimed.
- Logo preview uses a data URL compatible with the existing image CSP; no security policy was widened.

Browser company and provider results were explicitly synthetic localhost fixtures, kept outside the release. Source business and employee templates remained empty. Live integrations were not exercised.

Final UI refinements also verified default workflow selection, creation with custom owners, a completed research stage and reviewed offer stage, inline dialog error feedback, and a desktop composer remaining inside the viewport. The Customer engagement template adds a general research-to-offer-to-customer-handoff sequence for non-software businesses.

## Studio checks — 2026-10-03

- `test_studio.py` (12 tests) runs actual curl against a localhost fixture that mirrors OpenRouter's documented Image API, Video API (submit → poll → binary content), model catalogs and streamed chat usage. It covers saved assets and provider-reported costs, monthly budget enforcement before any provider call, malformed image rejection, video completion/failure/bounded retries, restart recovery, capability-bound expert tools, idempotent tool receipts, sandboxed page/SVG serving, cross-company and path-traversal 404s, zip export and Studio logos.
- Full suite: 100 tests passed (1 pre-existing skip). Python compilation and all five JavaScript syntax checks passed.
- Browser E2E (`tests/e2e/server.py` + `tests/e2e/run.js`, Playwright/Chromium, 24 checks): Mentor chat onboarding, one-click expert hiring, brand kit, live model catalog, logo generation and workspace logo, video render to playback, an engineer building a funnel page through the employee tool loop, sandboxed preview with inlined brand image (the page's attempt to read workspace state was blocked), task cost/asset detail, zip export, Brand launch kit availability, and no horizontal overflow at 390px. No console errors.
- Model IDs, parameters and pricing come from OpenRouter's public catalogs (57 image, 30 video, 398 tool-capable chat models on 2026-10-03). Live generation quality, billing and account access were not exercised: no real key was used.
- Independent code review findings fixed with regression tests: an unfinished hosted Mentor task no longer blocks a workspace from loading during the Mentor prompt upgrade; spend is recorded as soon as the provider bills (unusable image payloads, completed videos whose download fails) and exactly once; non-finite costs are ignored; tool calls cannot set parameters outside their schema (such as a pricier model or quality); transient video poll failures reset after a good poll; pages inline only images up to 8 MB; assets stream with HTTP Range and immutable caching; the zip export is built on disk; state snapshots omit raw usage rows; video submits are serialized; downloads only follow HTTPS redirects.
