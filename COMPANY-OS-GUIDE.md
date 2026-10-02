# CrewBot owner workflow

A fresh company has only Mentor. Supply the business name, offering, buyers/region, goals and current tools. Mentor saves answers incrementally and proposes a small team with concrete responsibilities. Review its saved proposal in AI employees before creating the crew. The native business form is an optional shortcut, not automatic team creation.

Settings manages per-company credentials, models, project ID, operating limits, email, colors, name and logo. A configured key is not verified service access. Slack/GitHub checks verify a selected destination; changing keys/scopes invalidates the check. Other providers are checked through actual requests.

Chat with Mentor to assign work, change roles and plan projects. Employee chat creates tracked work with a conversation link, owner, brief, output, errors and artifacts. Capabilities bound available tools. Product employees on OpenRouter draft source; execution requires the hosted OpenAI provider and API access.

Workflows resolve owners from your actual employees, including custom IDs. Unstaffed workflows cannot start. Prior-stage outputs and records pass forward; stages normally wait for review. Automatic internal handoffs require an owner setting and obey the task cap. Milestones retain owners and acceptance criteria; assigning twice reuses the existing task.

Connections & research supports Tavily, Firecrawl and Jev. Configure allowed Slack channels/GitHub repositories before enabling access. Employees propose external messages/issues; the review inbox shows exact content and destination before your approval.

Autopilot runs explicitly enabled recurring work. Reports are local unless sent through Resend to your configured recipient; automatic delivery needs owner authorization in Settings. Uncertain dispatches require inspection, with no automatic resend.

The server binds to localhost with host/origin checks and an action token. It is a single-owner local application. Keep it running for schedules; finish active work before restarting. Private data lives in ignored .crewbot/, separate from blank source templates. Public authentication, durable cloud workers and multi-user controls are future work.
