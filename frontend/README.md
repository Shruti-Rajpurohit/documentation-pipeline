# Patchwork Review Dashboard

Next.js App Router frontend for the documentation review API. It includes a sortable/filterable session queue and a three-pane review workspace with source context, current documentation, proposed changes, edit history, and role-gated actions.

## Setup

Install Node.js 20.9 or newer, then install the dependencies:

```sh
npm install
cp .env.example .env.local
```

Set `FASTAPI_BASE_URL`, `REVIEW_USER_ID`, and `AUTH_PROXY_SECRET` in `.env.local`. The ID and proxy secret are read only by the Next server-side route handler; the secret is not sent to browser code. Start the FastAPI service with the same proxy secret and ensure its repository allowlist and Ollama configuration are set.

For local development, run `sh scripts/dev.sh` from the repository root instead. It provisions/reuses a local `WRITER`, creates an ephemeral shared proxy secret without writing it to disk, and starts both services bound to loopback. This mode is for local development only; production must use your trusted authentication proxy and externally managed secrets.

The env-based user ID is a local single-user development adapter. For a multi-user deployment, replace it with a trusted Next.js auth/session integration that derives the database user ID per request; do not accept a user ID from client-controlled headers or request bodies.

## Run

```sh
npm run dev
```

Open `http://localhost:3000`. The API proxy is same-origin, checks POST Origin, limits request bodies, and forwards only the documented session endpoints.

## Checks

```sh
npm run typecheck
npm run lint
npm run build
```