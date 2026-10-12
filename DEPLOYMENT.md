# Upload and deploy this project

Local project: `/Users/ankitapandey/Projects/rahul3`

Repository: https://github.com/wooplix18-tech/wooplixautomation

## 1. Run locally

The local virtual environment and `.env.local` are prepared. Run:

```bash
cd /Users/ankitapandey/Projects/rahul3
./scripts/start.sh
```

Open http://127.0.0.1:8000/. Stop the server with Ctrl+C. For a fresh machine,
install Python 3.14, create `.venv`, and install `requirements.txt` first.

## 2. Upload the corrected source to GitHub

The changes are local until you commit/push or upload the source through GitHub.
Use the `wooplix18-tech` GitHub account and the `wooplixautomation` repository.
Keep the root-level configuration and the complete `app/` package together.
The clean archive at `dist/wooplixautomation-vercel.zip` contains source only;
extract it to upload its contents to GitHub. Vercel's Git import uses a
repository, not this ZIP file.

The ZIP intentionally excludes environment files, databases, virtual
environments, and account links. The optional Neon provisioning example is
independent of FastAPI; its original files remain in the working folder.

## 3. Import in the intended Vercel account

In that account, choose Add New → Project → import
`wooplix18-tech/wooplixautomation`. Confirm the team/account and repository
before clicking Deploy. Use these settings:

| Setting | Value |
| --- | --- |
| Root Directory | `.` (repository root; leave blank if the dashboard shows the root by default) |
| Framework | FastAPI |
| Python version | 3.14, from `.python-version` |
| Build / Install / Output | Leave framework defaults; no npm build |
| Entrypoint | `app.main:app`, already configured |

This follows the current [Vercel FastAPI setup](https://vercel.com/docs/frameworks/backend/fastapi)
and [Python runtime documentation](https://vercel.com/docs/functions/runtimes/python).

## 4. Import the environment

In Vercel's Environment Variables section, use Import .env and select the
private `.env.vercel` file from this folder. Never upload that file to GitHub.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Required for persistent hosted projects; existing pooled Neon URL |
| `DATABASE_URL_UNPOOLED` | Optional direct connection for future migrations; runtime does not use it |
| `GROQ_API_KEY`, `GROQ_MODEL` | Optional Groq enhancement provider |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `AI_MODEL` | Optional OpenAI-compatible enhancement provider |
| `CORS_ORIGINS` | Optional comma-separated origins for other API clients; dashboard needs no extra origin |

Blank AI keys mean deterministic demo mode. The saved `NEON_AUTH_*` and
`NEON_FUNCTION_API_BASE_URL` values are unused by FastAPI and can be omitted
from the import. Do not import `AUTOMATION_DB_PATH` from `.env.local`.
Vercel supplies `VERCEL` automatically.

For previews, use a separate Neon development branch if you want to isolate
test projects. This folder currently has matching local and Vercel connection
settings; local app actions therefore use the same shared database by default.

This app currently has no application login. Use Vercel deployment protection
for a private workspace; the Neon Auth example does not protect FastAPI routes.

## 5. Verify after deployment

1. Open `/api/health`: expect HTTP 200, `storage_backend: neon_postgres`, and
   `durable_storage: true`.
2. Open `/`: the dashboard should load its project list.
3. Paste a brief into the project assistant or upload a requirements document.
   Click Generate delivery pack. Expect seven artifacts and `pack_ready` status.
4. Reload the dashboard and confirm that the project remains.

For CLI deployment, run `vercel login` using the intended account, then
`vercel` from the project root. Select the intended team/project and configure
its hosting environment first. Use the returned preview URL for verification
before choosing a production release. No account is linked by this preparation.

## Local validation performed

- 30 offline API/workflow/environment/document intake regression tests.
- Dashboard JavaScript syntax check.
- Browser walkthrough: create, approve scope, approve delivery, fail UAT,
  pass UAT, then reload; seven artifacts persisted with no console errors.
- Conversational inquiry, follow-up questions, text-file upload, and automatic
  generation verified in the browser.
- Read-only connection check against the saved Neon database and confirmation
  that the application table exists.
- PostgreSQL lifecycle and stale-save verification in a session-only temporary
  table; existing project rows were untouched.

AI-provider calls and a cloud Vercel build are not part of the offline checks.

Automatic generation prepares planning documents and an implementation
blueprint. It does not build or deploy a generated application, and it does
not claim that client UAT has passed.
