# Automated Documentation Pipeline

A FastAPI service that compares product repository revisions, finds related documentation, asks a local model for an evidence-linked patch, and persists review sessions and edits in SQLite. Approved patches are applied only after an approver explicitly approves them.

## Requirements

- Python 3.10 or newer
- Git
- Ollama with a pinned model available locally
- A source repository with at least two commits
- A documentation working tree containing Markdown, MDX, reStructuredText, or AsciiDoc files
- A trusted authentication proxy that injects the authenticated database user ID

Install the service dependencies:

```sh
python -m pip install -r requirements.txt
```

Install the HTTP test client as well when running the integration suite:

```sh
python -m pip install -r requirements-dev.txt
```

## Run

Start Ollama and pull the model you want to evaluate:

```sh
ollama pull "MODEL:VERSION"
```

Set a stable proxy secret and provision users for your authentication proxy to map to:

```sh
export AUTH_PROXY_SECRET="replace-with-a-long-random-secret"
export REPOSITORY_ROOTS="/srv/product-repositories,/srv/documentation-repositories"
python -m app.db.provision_user writer writer@example.com WRITER
python -m app.db.provision_user reviewer reviewer@example.com REVIEWER
python -m app.db.provision_user approver approver@example.com APPROVER
```

The API trusts `X-Authenticated-User-ID` only when accompanied by the matching `X-Auth-Proxy-Secret`. Put it behind a trusted proxy that strips client-supplied identity headers, injects its own values, and does not expose the service directly. `REPOSITORY_ROOTS` is a comma-separated allowlist; both requested repositories must resolve beneath one of these directories. The service binds to loopback by default.

Run the API:

```sh
python -m app.main --host 127.0.0.1 --port 8000
```

The SQLite database defaults to `./data/documentation_pipeline.db`; override it with `DATABASE_URL`. Tables are created on startup. OpenAPI docs are available at `http://127.0.0.1:8000/docs`.

Create a review by posting JSON to `/sessions` with `source_repo`, `docs_repo`, `base_revision`, `head_revision`, `model_name`, and optional `ollama_url`. Include the two trusted-proxy headers. `POST /sessions/{id}/edit` accepts a `document_edit_id` and new `content`; `/approve` requires `APPROVER`; `/reject` requires `REVIEWER` or `APPROVER`.

## Review Behavior

- Source diffs are bounded to 100 files and 250 KB.
- Documentation search is exact-term based and bounded to 1,000 files and 1 MB per file. Exceeding the scan limit stops for human investigation.
- Model proposals must be valid JSON, cite supplied source evidence, and target a retrieved document section. The patch writer independently restricts targets to existing documentation files under the configured docs root.
- Previewing a patch does not modify files. Editing updates only the draft. Rejecting closes the review without writing. Approval checks that reviewed files have not changed since the preview and then applies the patch.
- Roles are `WRITER` (create/edit), `REVIEWER` (edit/reject), and `APPROVER` (approve/reject). Every successful content edit records actor, previous content, new content, and timestamp.
- Session diffs, repository paths, original patch snapshots, model response, proposal, edits, and audit events are persisted so review state survives process restarts.

## Current Limitations

- Publishing currently applies approved changes to the docs working tree only. It does not create a commit, branch, pull request, or push to GitHub. The docs destination and publication policy still need to be selected.
- There is no GitHub Actions/webhook trigger; runs are manual.
- SQLite schema creation currently uses SQLAlchemy `create_all`; add and deploy Alembic migrations before evolving an existing production database.
- Authentication is delegated to a trusted proxy; the proxy secret and header-stripping policy are security-critical.
- Retrieval uses exact identifiers and changed file names, not embeddings or a vector database. Missing or ambiguous matches stop for human investigation.
- The analyzer uses conservative file-type heuristics. It does not perform semantic OpenAPI compatibility analysis.
- Suggested checks are displayed but are not executed automatically.
- No real Ollama quality evaluation has been run yet; choose and benchmark a pinned model for the target repository before relying on its drafts.

## Tests

Run the standard-library test suite:

```sh
python -m unittest discover -s tests -v
```

## Frontend

The Next.js dashboard lives in [`frontend/`](frontend/README.md). Install Node.js 20.9 or newer, follow that README to configure the server-side API proxy, then run `npm install` and `npm run dev` from `frontend/`.