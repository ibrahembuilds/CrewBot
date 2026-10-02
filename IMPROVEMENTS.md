# CrewBot improvement roadmap

CrewBot currently runs as a local owner workspace. A new company starts with a blank business profile and Mentor, then the owner reviews a proposed employee team before creating it. Saved company work is scoped by workspace; this is not authenticated tenant isolation for a public service. Provider HTTP requests use curl. OpenRouter streams text and bounded function calls; it does not provide a shell or computer. OpenAI-hosted Agents sessions provide the separate hosted runtime. Dashboard updates use the application's event/state endpoints. Scheduled work and report delivery depend on the local server remaining running.

The following are product and engineering priorities, not claims of existing capabilities.

| Priority | Improvement | Acceptance criteria |
| --- | --- | --- |
| 1 | Durable hosting and workers | Move scheduling and execution out of the browser/local process into a supervised worker. Persist jobs in a transactional database and queue. Recover interrupted jobs without repeating external writes. |
| 1 | Identity and authorization | Before a public deployment, add sign-in, company membership, per-role permissions, server-side company access checks, TLS, and a managed secret store. Keep credentials and provider projects separate for each company. |
| 1 | Backups and export | Offer a company export without credentials, restore validation, versioned database migrations, and encrypted backups. Test restoration and document retention/deletion. |
| 1 | Usage and cost controls | Capture provider-reported usage, attribute it to employee/task/company, enforce configurable spending and concurrency limits, and stop unattended work when budgets are reached. Existing task/tool limits do not guarantee a monetary budget. |
| 2 | OAuth and scoped connectors | Replace manually entered connector tokens with supported OAuth flows where possible. Show granted permissions and restrict destinations. Add token expiry/revocation handling; never infer write access from a connected badge. |
| 2 | Event-driven work | Add Slack/GitHub webhooks with signature verification, replay protection, persisted event deduplication, and a visible rule mapping events to employee responsibilities. Avoid creating duplicate tasks after webhook retries. |
| 2 | External CRM integration | Add a connector for a chosen CRM, with field mappings, account/contact deduplication and source provenance. Present exact proposed record changes for approval before writing. Do not present the built-in lead store as a synced external CRM. |
| 2 | Safer approvals and retries | Retain the exact action, destination, payload and receipt for every external write. Distinguish rejected, failed and uncertain outcomes. Provide a reconciliation view for uncertain actions; never automatically repeat an ambiguous send or create. |
| 3 | Knowledge and evidence | Add permission-scoped document retrieval, citations, freshness dates and an explicit distinction between owner-provided facts and retrieved material. Test prompt-injection resistance with hostile documents. |
| 3 | Better team recommendations | Evaluate Mentor proposals against a business-specific rubric: responsibilities, expected output, required tools, dependencies and overlap. Let the owner preview changes before applying them. |
| 3 | Role and workflow evaluation | Maintain representative synthetic business scenarios, handoff cases, provider failures and approval cases. Compare saved output quality before changing prompts or default models. |
| 3 | Reporting reliability | Current email delivery uses Resend with owner-configured sender/recipient settings; SMTP is not implemented. Add delivery webhooks, bounce handling, verified sender guidance and report-specific idempotency. Show receipt versus final delivery separately. |
| 4 | Design and accessibility | Add keyboard-only and screen-reader checks, contrast checks across custom brand colors, readable empty/error states, and mobile layouts for long task and connector details. |
| 4 | Observability | Add structured redacted logs, correlation IDs, queue health, provider latency/error metrics and a task timeline. Keep full prompts and customer documents out of logs by default. |

## Release boundary

Local fixture tests establish protocol handling and saved-state behavior, not provider account access or commercial research quality. Live deployment requires verifying model availability, provider permissions and sender configuration with the owner's credentials. A cloud computer, arbitrary browser automation, external CRM sync and public multi-company authentication should only be advertised after their corresponding implementation and tests exist.
