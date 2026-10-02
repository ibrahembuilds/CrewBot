# CrewBot release checklist

## Before publishing source

- Run `python -m unittest discover -v` and the browser JavaScript syntax checks in CI.
- Verify `company.json` is blank and `employees.json` is an empty array. A new runtime should create only Mentor and ask about the business before proposing employees.
- Keep `.crewbot/`, credentials, company history, team session state, downloads, generated reports and local environment files out of Git and release archives.
- Review `git diff --cached` and the final file list. Never publish real provider keys, provider project IDs, agent/session IDs or a previous owner's company profile.
- Use synthetic fixture companies and localhost provider endpoints for automated tests. Do not require repository secrets or contact business services in CI.
- Confirm that documentation describes current capabilities, the local-server requirement, provider setup and the distinction between OpenRouter tools and OpenAI-hosted execution.

## Before using live providers

- Enter credentials through Settings or environment variables; rotate any credential previously pasted into a chat or public repository.
- Verify each selected model exists for the configured account. A configured key indicator is not proof of access.
- Configure allowed Slack channels/GitHub repositories before requesting an external write. Review exact destination and content.
- Set a verified Resend sender and the intended report recipient before enabling automatic reports. Check a first report and receipt.
- Start with a low task/tool limit and review handoffs before enabling unattended internal work.
- Keep the server supervised and take a backup before operational use. Authentication and a durable database/queue are required before exposing this local workspace publicly.

## After publishing

- Confirm the GitHub workflow passes on both supported runner operating systems.
- Run a fresh checkout with no keys and confirm that the landing page and blank-company Mentor experience open.
- Test company branding and tenant-scoped state with two synthetic companies.
- Record which live integrations were verified; leave untested services explicitly unverified.
