# CrewBot connections

Configure credentials in **Settings → API keys**, then allow the destinations in **Connections**. A key being present means **configured**, not that its permissions, balance, model access or destination have been validated. No key is bundled in this repository.

| Connection | Setup and permissions | What CrewBot does |
| --- | --- | --- |
| OpenRouter | Add `OPENROUTER_API_KEY` or enter the key in Settings. Select an available tool-capable chat model. | Streams employee chat and calls the local tools assigned to each employee. It does not provide a cloud browser or shell. |
| OpenAI Agents | Add `OPENAI_API_KEY`, choose OpenAI as the provider and enter your own OpenAI project ID. The project needs Agents access and the requested model. | Creates reusable employee definitions, starts hosted sessions using their returned IDs, streams events and handles tool calls. |
| Tavily | Add `TAVILY_API_KEY`. Your account needs search credits. | Searches the web with a bounded result list and source URLs. Findings still need review. |
| Firecrawl | Add `FIRECRAWL_API_KEY`. Your account needs scrape credits. | Fetches markdown from a supplied public HTTP(S) page. Login-only pages, private URLs and CAPTCHA handling are not supported by this integration. |
| Slack | Install a Slack app with a bot token; add `SLACK_BOT_TOKEN`. Grant `channels:history` for public channels, `groups:history` for private channels and `chat:write` for approved messages. Invite the bot into each selected channel. Save exact channel IDs in Connections and enable it. | Reads a limited channel-history page and prepares messages for human review. The Test action checks the configured connection; inspect permission errors before enabling scheduled work. |
| GitHub | Use a fine-grained token restricted to selected repositories. Add `GITHUB_TOKEN`; grant repository Metadata read and Issues read. Grant Issues write only if you want approved issue creation. Save `owner/repository` destinations and enable it. | Reads open issues and creates reviewed issues. This connector does not push code, merge pull requests or grant repository access. |
| Project management | No external key needed. | Stores company projects, milestones, leads, offers, tasks and handoffs locally. It is not an Asana, Linear or Jira connector. |
| Resend | Add `RESEND_API_KEY` with sending access. Verify the sender domain with Resend. Set a sender and your report recipient in Settings. | Sends a generated report only when you click Send or explicitly enable automatic reports. Receipt IDs are saved. |

Primary references: [OpenRouter tools](https://openrouter.ai/docs/guides/features/tool-calling), [OpenAI Agents](https://developers.openai.com/api/docs/guides/agents-api/overview), [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search), [Firecrawl Scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape), [Slack history](https://docs.slack.dev/reference/methods/conversations.history/), [GitHub issues](https://docs.github.com/en/rest/issues/issues?apiVersion=2022-11-28), [Resend email](https://resend.com/docs/api-reference/emails/send-email).

## Jev via OpenRouter

`typesafe/jev-router` is the chat routing option. It chooses an underlying generation model; its availability, latency and price depend on your OpenRouter account and the routed model. The separate `typesafe/jev-1.13` decision model is used by the optional employee-routing tool through `/alpha/decisions`. It returns a recommendation among existing employees; it does not itself generate an employee conversation or complete their work. Create your team before using employee routing. See [Jev Router](https://openrouter.ai/typesafe/jev-router) and [OpenRouter’s Jev explanation](https://openrouter.ai/blog/insights/what-is-jev/).

## Credential storage

- **Memory:** keys entered without Remember stay in this running server only.
- **Protected:** Remember uses Windows DPAPI under the current Windows account. On other operating systems, use memory or environment variables. Do not commit or distribute `credentials.dpapi.json`.
- **Environment:** the default workspace may read provider environment variables. Additional company workspaces do not inherit these credentials.
- **Remove:** deletes the company’s saved key and persists a non-secret disabled marker. An environment variable cannot silently restore it on restart. Enter a key again to re-enable that provider.

Credentials are passed to curl through its stdin configuration, not command-line arguments. HTTP destinations are fixed provider origins. All network calls use curl, with bounded response sizes and timeouts. Logs, tool outputs and surfaced provider errors redact known credentials. Keep API keys out of employee instructions and chat.

## Review and scheduling

Slack posts and GitHub issues require a saved approval for their exact destination and content. Enabling a connection is not approval to publish arbitrary messages. Background schedules need the CrewBot server to remain running. Automatic internal handoffs and scheduled email reports are separate settings; configure them deliberately after a manual task works.

If an external write times out, disconnects or is interrupted by restart, its status can be **uncertain**. CrewBot does not automatically repeat that write. Check the provider’s history before deciding whether to create a new action. Resend requests include a stable per-report idempotency key, but Resend’s protection expires after 24 hours; this is not an unlimited replay guarantee. [Resend idempotency headers](https://resend.com/docs/api-reference/emails/send-email).

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| Key present, task blocked | Check the provider’s account balance, model availability, project access and error text. Key presence alone does not validate them. |
| `401` / invalid token | Replace the key in the correct company’s Settings. Do not paste it into Mentor chat. |
| `403` / missing permissions | Check project or repository access, Slack bot scopes and channel membership. Reinstall a Slack app after changing its scopes. |
| `429` | Check provider credits and rate limits. Review saved work before manually retrying a task. |
| Stream ends early or tool output is truncated | Partial text is retained. Malformed or truncated tool batches are blocked before execution. Inspect saved work before continuing. |
| Slack identity succeeds but history fails | Verify the history scope for the channel type and invite the bot to that channel. Identity verification does not grant conversation access. |
| Protected key cannot be read | Open CrewBot under the original Windows account or re-enter the credential. One unreadable provider does not discard other valid keys. |
| Schedule stays idle | Check the employee’s selected provider key, enabled state, due time, daily cap and whether its previous task is awaiting review. |
| Email does not arrive | Confirm the verified sender, recipient, Resend receipt and provider delivery logs. A send receipt does not prove inbox delivery. |

Automated connector checks use a localhost fixture, never real business accounts. Live access must be checked with your credentials and allowed destinations.
