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
