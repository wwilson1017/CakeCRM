# Contributing to CakeCRM

Thanks for your interest in CakeCRM — a free, open-source, self-hostable CRM with a
built-in AI sales assistant. This guide covers how to set up a development
environment, the checks your change needs to pass, and how to sign off your commits.

## License & the DCO (not a CLA)

CakeCRM is licensed under **[AGPL-3.0](LICENSE)**. Contributions are accepted under
the **Developer Certificate of Origin (DCO)** — there is **no CLA**. Your contribution
is licensed to the project under the same AGPL-3.0 terms as the rest of the codebase
(inbound = outbound).

### Developer Certificate of Origin (DCO)

The DCO is a lightweight, industry-standard alternative to a CLA. By signing off on a
commit you certify that you wrote the change (or have the right to submit it) under
the project's license — the full text is at <https://developercertificate.org/>.

**Signing off is one flag on your commit:**

```bash
git commit -s -m "Your commit message"
```

`-s` appends a line like this to the commit message, using your `git config user.name`
and `user.email`:

```
Signed-off-by: Jane Contributor <jane@example.com>
```

Every commit in a PR must carry a `Signed-off-by` line that matches its author. If you
forget on the last commit, `git commit --amend -s` fixes it; for several commits,
`git rebase --signoff main` signs off the whole branch. A DCO check runs on pull
requests and will point out any commits that still need a sign-off.

> Please do **not** add `Co-Authored-By` trailers or tool-generated footers — keep
> commit messages to the change itself plus the `Signed-off-by` line.

## Development setup

CakeCRM is a FastAPI (Python 3.12) backend + a React/Vite (TypeScript) frontend, backed
by **PostgreSQL** (mandatory — the backend refuses to start without `DATABASE_URL`).

1. **Start Postgres** (Docker Compose provisions the dev database):

   ```bash
   docker compose up -d
   ```

2. **Configure environment** — copy the example and set at least `AUTH_PASSWORD`
   (the default `DATABASE_URL` already matches docker-compose):

   ```bash
   cp .env.example .env
   ```

3. **Backend** — create a virtualenv and install runtime + dev dependencies:

   ```bash
   cd backend
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```

4. **Frontend** — install dependencies:

   ```bash
   cd frontend
   npm ci
   ```

5. **Run the app** — from the repo root, `python run.py` starts the backend (and, in
   production mode, serves the built frontend). For frontend hot-reload during
   development, run `npm run dev` in `frontend/` alongside it.

## Checks your change must pass

Every pull request runs [CI](.github/workflows/ci.yml) with three gates. Run them
locally before you push:

**Backend** (from `backend/`, with the virtualenv active):

```bash
ruff check .          # lint + import order (config in ruff.toml)
python -m pytest -q   # tests (run from backend/ so `import main` resolves)
```

`ruff check --fix` auto-fixes import ordering and other safe issues. Please fix root
causes rather than adding `# noqa` suppressions.

CI also runs a fast import check (imports the app and core modules with no
`DATABASE_URL`) as a safety net before the tests. You don't need to run it
separately — `python -m pytest -q` imports the app, so a green test run covers it.

**Frontend** (from `frontend/`):

```bash
npm run build   # tsc type-check + vite build
npm run lint    # eslint
```

**Secret scanning** — CI runs [gitleaks](https://github.com/gitleaks/gitleaks) on every
PR and blocks anything that looks like a committed secret. Never commit real API keys,
passwords, `.env` files, or customer data. If you need to reference a value, use an
obvious placeholder (the dev credentials in `.env.example` and `docker-compose.yml` are
intentionally fake).

## Opening a pull request

1. Branch from `main` (e.g. `feature/short-description` or `fix/short-description`).
2. Keep the PR focused — one logical change per PR.
3. Sign off your commits (`git commit -s`) and fill out the PR template.
4. Make sure all CI checks are green. Maintainers merge PRs — please don't merge your
   own.

## Scope & product rules

CakeCRM has a deliberately tight scope. Before proposing a feature, note these rules
(see [CLAUDE.md](CLAUDE.md) for the full list):

- **The assistant is a feature of the CRM, not the product.** There is one built-in
  assistant — no multi-agent roster.
- **The CRM must be fully usable with zero AI keys.** AI features degrade gracefully
  when no provider is configured.
- **Gmail is read + draft only, forever.** There is no email-send tool anywhere in the
  codebase, and none may be added.

Questions? Open an issue using one of the templates, or start a discussion.
