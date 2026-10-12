# Proposal agent source

Imported from https://github.com/wooplix15-debug/proposalagent at commit
`0ab21f9c70ced79c93bed2b18f1f71d6831c259a` on 2026-10-12, at the repository owner’s request.

Includes the complete generation graph, specialist contracts, critic/repair
logic, source reconciliation, and Python DOCX/HTML renderers. Relative imports
isolate this package; dotenv loading is owned by the host application.
Telegram delivery, standalone web app/checkpointer, PHP PDF renderer, secrets,
and account links were not activated. CRM precedent and delivery records are
not used by the integration unless explicitly configured in future work.

The configured production path retains GPT-OSS production fallbacks and omits
the upstream Qwen preview fallback.
