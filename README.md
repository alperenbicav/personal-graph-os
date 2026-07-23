# Personal Graph OS

A **local-first, graph-first personal workspace**. Every task, note, project, decision,
research resource, and file lives as a typed node or edge in one canonical SQLite database on
your own machine. Canvas, Table, Kanban, Timeline, Search, Research, and Activity are all just
different *views* (projections) over that same data — nothing is duplicated or converted
between them.

There is no cloud account, no hosted sync, and no server other than the one you run yourself on
`127.0.0.1`. Your data stays on your disk unless you explicitly export, back up, or proxy the
server to another device.

- **Backend**: Python (FastAPI, SQLite, no ORM) — REST API + an [MCP](https://modelcontextprotocol.io)
  server for AI agents.
- **Frontend**: React + TypeScript + Vite, served same-origin by the backend under `/app/`, and
  installable as a Progressive Web App.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Core concepts](#core-concepts)
- [Feature tour](#feature-tour)
- [Quick start](#quick-start)
- [Everyday usage guide](#everyday-usage-guide)
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [REST API](#rest-api)
- [MCP (AI agent) integration](#mcp-ai-agent-integration)
- [Backup, export, and portability](#backup-export-and-portability)
- [Remote access](#remote-access)
- [Security model](#security-model)
- [Development](#development)
- [Testing and the release gate](#testing-and-the-release-gate)
- [Project structure](#project-structure)
- [Troubleshooting / FAQ](#troubleshooting--faq)
- [Roadmap / non-goals](#roadmap--non-goals)

---

## Why this exists

Most personal knowledge/task tools force you to pick one shape for your data: a Kanban board,
a wiki, a note graph, a task list. The moment you need a second shape, you end up copying data
between two tools and they drift apart.

Personal Graph OS instead keeps **one typed graph of nodes and edges** as the single source of
truth. A "task" and a "research note" are both just nodes with a schema; every view (Canvas,
Table, Kanban, Timeline, Search) reads that same graph and renders it differently — there is no
second copy to keep in sync, and no export/import step between your task list and your notes.

## Core concepts

| Concept | What it means |
|---|---|
| **Node** | A typed object (Task, Note, Project, Decision, Resource, …). Has a title, a status, custom fields, and optional archive state. |
| **Edge** | A typed, directed relationship between two nodes (`relates_to`, `supports`, `implements`, `blocks`, …), each with an inverse name for the reverse direction. |
| **Node type / Field / Status** | User-editable schema. You can add new node types, custom fields (text/number/boolean/date/select/URL/file path/object reference), and custom statuses through the Schema editor — no code change needed. |
| **Canvas / Placement** | A node can be placed on one or more canvases at an (x, y) position — the visual graph view. A node doesn't have to be placed anywhere to exist. |
| **Resource** | The backing record for anything you're *researching* (an article, a repo, a paper). A Resource always owns exactly one Node (graph/content/archive lives on the node; research lifecycle lives on the Resource). |
| **Discovery** | Import external candidates (a list of URLs/titles you already have) as Resources + Nodes, with a preview step before you commit. This app never fetches URLs or searches the web itself. |
| **Saved view** | A named, reusable projection (view kind + filter/sort) you can re-open later. |
| **Attachment** | A file you upload, copied into the workspace's managed storage and checksummed. |
| **File reference** | A *non-copying* pointer to a file that lives elsewhere (e.g. in a git repository on your machine) — recorded by machine name + relative path, optionally verified against an absolute path. |
| **Context Pack** | A bounded, immutable snapshot of selected nodes/edges/evidence, built for handing off to an AI agent over MCP. |
| **Activity event** | An append-only audit log entry for every mutation. Certain actions can be undone, which appends a compensating event rather than rewriting history. |

## Feature tour

- **Graph canvas** — drag nodes around, connect them with typed edges, multiple canvases per
  workspace, keyboard-operable object list as an accessible alternative to dragging.
- **Schema editor** — add/edit node types, fields (8 field types including `object_reference`
  and `select`), statuses, and edge types, all from the UI.
- **Structured views** — Table, Kanban (grouped by status), and Timeline (grouped by date),
  each a live projection of the same graph, with saved views.
- **Research workflow** — a dashboard of resources by lifecycle status, discovery
  preview/import, a guided "next step" workflow chain (Resource → Takeaway → Decision → Task →
  Implementation) and a detail panel per resource.
- **Search** — full-text search across nodes and resources.
- **Files** — managed attachments (uploaded, copied, checksummed, downloadable) and file
  references (provenance pointers to files that live outside the app, with on-demand
  verification).
- **Activity & undo** — every mutation is recorded; supported actions can be safely undone
  (adds a compensating event, never rewrites history).
- **Export & backup** — a portable `.zip` export of your whole workspace (manifest-verified,
  secrets excluded) and a `pgos-backup` CLI for create/verify/restore.
- **Agent-native MCP server** — the same data, exposed as bounded, authenticated MCP tools so
  an AI agent can read, capture, connect, import, and build Context Packs — with idempotent
  mutations (safe to retry) and full attribution in the activity log.
- **Desktop PWA** — installable, works offline for the app shell, same-origin auth, with an
  update-ready prompt when a new build is deployed.
- **Remote access (optional)** — documented pattern for reaching your own instance from another
  device through a self-managed HTTPS reverse proxy (see [Remote access](#remote-access)).

## Quick start

### Prerequisites

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) (dependency management + running the backend)
- Node.js **18+** and `npm` (for the frontend)

### 1. Clone and install

```bash
git clone https://github.com/alperenbicav/personal-graph-os.git
cd personal-graph-os

# Backend: creates .venv and installs from pyproject.toml/uv.lock
uv sync

# Frontend
cd frontend
npm install
cd ..
```

### 2. Build the frontend once

The backend serves the frontend's production build same-origin under `/app/`. Build it once
(and again after any frontend change you want reflected there):

```bash
cd frontend
npm run build
cd ..
```

### 3. Run the backend

```bash
uv run python -m personal_graph_os.api
```

You'll see:

```
Personal Graph OS API listening on http://127.0.0.1:8000
The bearer token is never printed here. Run `pgos-token show` to read it.
```

The server binds to `127.0.0.1` only (loopback) — it never accepts a direct LAN or public
connection.

### 4. Get your access token and unlock the app

```bash
uv run pgos-token show
```

Open **http://127.0.0.1:8000/app/** in your browser, paste the printed token into the unlock
screen, and you're in. The token is held in memory and, once verified, committed to that
browser tab's own history — it is never written to a cookie, `localStorage`, or the build.

That's it — your data lives in `workspace/graph.db` (created on first run) and everything you do
from here on is local.

### (Optional) Frontend-only dev loop

If you're actively editing the frontend, run Vite's dev server with hot reload against the
same backend instead of rebuilding on every change:

```bash
# Terminal 1 — backend with CORS enabled for the Vite dev server's origin
PGOS_DEV_CORS=1 uv run python -m personal_graph_os.api

# Terminal 2 — Vite dev server
cd frontend
cp .env.local.example .env.local   # first time only
npm run dev
```

Open the URL Vite prints (typically `http://localhost:5173`) and unlock with the same token.

## Everyday usage guide

This section walks through the product itself, once it's running at `/app/`.

### Capturing something

The top bar has a **capture** field with a type selector (defaults to Task/Note/Project/
Resource, plus any custom types you've added). Type a title, pick a type, hit **Add** (or
Enter) — it's created and placed on the active canvas.

### Working the canvas

- Drag a node card to reposition it.
- Drag from a node's connection handle to another node's handle to open the **Connect** dialog;
  pick a relationship type and confirm (or press Escape to cancel).
- Click a node to select it — the **Inspector** panel opens on the right with its fields,
  status, relations, and Files section.
- The canvas rail lets you create additional canvases and place an already-captured node onto
  the active one (via "existing object to place") without re-capturing it.
- Everything on the canvas is also reachable through a keyboard-operable object list (an
  accessible alternative to drag-and-drop), which is what screen readers and keyboard-only
  users should use instead of the raw drag gesture.

### Editing the schema

Open **Schema** from the top bar to:

- create a new node type (name, icon, color);
- add/edit/remove its fields (text, number, boolean, date, select, object reference, URL, file
  path) and whether a field is "required once set" (a node can omit it forever, but can't clear
  it back to empty once given a value);
- add/edit/remove its statuses (name, color, terminal flag, sort order);
- create/edit/remove edge types (name + inverse name + color).

Every node type you create is immediately usable for capture — there's no separate "activate"
step.

### Structured views

Use the nav tabs to switch between:

- **Table** — every node as a row; also where you'll find the **Saved views** panel (name a
  view and save it for later).
- **Kanban** — nodes grouped into columns by status.
- **Timeline** — nodes ordered by their creation date.
- **Search** — full-text search across nodes and resources.

### Research workflow

- **Discovery**: paste a list of candidates, one per line — `identifier | title | description
  (optional)` (identifier is typically a URL) — along with an instruction string, click
  **Preview** to see what would be imported, then **Confirm import**. This is the only way a
  Resource gets created from external candidates — the app never fetches or searches the web on
  your behalf.
- **Research**: a dashboard of every Resource by lifecycle status, with a detail panel per
  resource and a guided **workflow chain** button that proposes the next step (e.g. turn a
  reviewed resource's takeaway into a Decision, then a Task, then an Implementation) instead of
  making you manually recreate the same node/edge pattern each time.

### Files

Select a node, open its **Files** section in the Inspector:

- **Attachments** — upload a file; it's copied into the workspace's managed storage,
  checksummed, and downloadable/removable from here.
- **File references** — record a pointer to a file that lives elsewhere (machine name +
  relative path, optional repository name/git ref/absolute path). Click **Verify** to check an
  absolute-path reference is still present on disk. Nothing here copies the referenced file's
  bytes.

### Activity and undo

Open **Activity** to see every mutation as an append-only log. Select an event with an
**Undo** button, confirm with a reason — this appends a compensating event rather than
rewriting history. An event can become permanently non-undoable (e.g. already reversed, or its
pre-image was too large to keep) — the UI shows exactly why.

### Export

Click **Download export** (in the top bar) at any time to get a portable `.zip` of your entire
workspace — see [Backup, export, and portability](#backup-export-and-portability) for exactly
what's included and excluded.

## Configuration

All configuration is through environment variables read by `personal_graph_os.api.__main__`;
every one is optional and has a safe local default.

| Variable | Default | Purpose |
|---|---|---|
| `PGOS_DB_PATH` | `workspace/graph.db` (relative to cwd) | Path to the SQLite database file. Point this at an isolated path to run a second, independent workspace. |
| `PGOS_PORT` | `8000` | Port the backend listens on (always bound to `127.0.0.1`). |
| `PGOS_STATIC_DIR` | `frontend/dist` if present | Directory of the built frontend to serve under `/app/`. If unset and no build is found, the server runs API/MCP-only (no UI). |
| `PGOS_TRUSTED_HOSTS` | `127.0.0.1,localhost` | Comma-separated `Host` header allowlist. Requests with any other `Host` get `400`. Add your proxy's hostname here for remote access — see below. |
| `PGOS_DEV_CORS` | unset | Set to `1`/`true`/`yes` to enable CORS, only for local development against a separately-hosted Vite dev server. Never set this for a production/remote deployment. |
| `PGOS_DEV_CORS_ORIGINS` | Vite's default dev origins | Comma-separated origin allowlist, only consulted when `PGOS_DEV_CORS` is enabled. |

The bearer token itself is **never** an environment variable or a build-time value — it's
generated on first run and persisted as a file (`api-token`, mode `0600`) next to the database.
Use `pgos-token show`/`rotate` to read or change it.

## CLI reference

Two console scripts are installed by `uv sync` (see `pyproject.toml`'s `[project.scripts]`):

### `pgos-token` — read or rotate the bearer token

```bash
uv run pgos-token show                    # print the current token
uv run pgos-token rotate                  # generate + persist a new token
uv run pgos-token show --db-path path/to/graph.db   # target a non-default workspace
```

Rotation only replaces the token *file*. A running server holds its token in memory for its
whole process lifetime, so rotating takes effect only after you **stop, rotate, then restart**
the server — every previously-unlocked browser tab gets `401` on its next request and returns
to the unlock screen.

### `pgos-backup` — create, verify, restore

```bash
uv run pgos-backup create  <workspace_dir> <output_dir>     # writes a timestamped archive
uv run pgos-backup verify  <backup_path>                     # checks archive integrity, no changes
uv run pgos-backup restore <backup_path> <destination_dir>   # destination must be empty
```

See [Backup, export, and portability](#backup-export-and-portability) for what's included.

## REST API

The backend exposes a conventional JSON REST API (FastAPI, OpenAPI schema at `/openapi.json`
when the server is running) under these resource groups — every route requires the bearer
token (`Authorization: Bearer <token>`):

| Prefix | Covers |
|---|---|
| `/workspace` | The single local workspace and its full schema (node types/fields/statuses, edge types). |
| `/node-types`, `/edge-types` | Schema mutation: create/update/delete node types, fields, statuses, edge types. |
| `/nodes`, `/edges` | Create/update/archive nodes; create edges. |
| `/canvases`, `/canvases/{id}/placements` | Canvases and node placements on them. |
| `/placements/{id}` | Move a placement. |
| `/resources` | Research resources (create, update, archive, get, list). |
| `/discovery/preview`, `/discovery/apply` | Two-step discovery import. |
| `/research/dashboard`, `/research-settings` | Research dashboard data and settings. |
| `/views/table`, `/views/kanban`, `/views/timeline` | Evaluate a projection (optionally by saved view). |
| `/workflow-chain/advance` | Advance the guided research→decision→task→implementation chain. |
| `/saved-views` | CRUD for saved views. |
| `/search` | Full-text search. |
| `/activity-events`, `/activity-events/{id}`, `/activity-events/{id}/undo` | Activity log and undo. |
| `/nodes/{id}/attachments`, `/attachments/{id}/download` | Managed file attachments. |
| `/nodes/{id}/file-references`, `/file-references/{id}/verify` | Non-copying file references. |
| `/export` | Portable workspace export (streams a `.zip`). |
| `/mcp` | The MCP Streamable HTTP endpoint — see below. |

The exact request/response shapes are best read from `/openapi.json` against your own running
instance, or from the corresponding router module under `src/personal_graph_os/api/routers/`.

## MCP (AI agent) integration

Personal Graph OS exposes an authenticated [Model Context Protocol](https://modelcontextprotocol.io)
server at `/mcp` (Streamable HTTP transport) so an AI agent can work with your graph directly,
under the same bearer-token authentication as the REST API.

Bounded read tools:

`pgos_get_workspace`, `pgos_list_nodes`, `pgos_get_node`, `pgos_list_edges`, `pgos_search`,
`pgos_list_resources`, `pgos_get_resource`, `pgos_list_node_evidence`,
`pgos_list_activity_events`, `pgos_get_activity_event`

Attributed, idempotent mutation tools (each requires an `actor_name`, a `reason`, and a stable
`request_id` — replaying the same `request_id` returns the original result instead of creating
a duplicate):

`pgos_create_node`, `pgos_update_node`, `pgos_archive_node`, `pgos_connect_nodes`,
`pgos_create_or_reuse_resource`, `pgos_update_resource`, `pgos_archive_resource`,
`pgos_advance_workflow`, `pgos_preview_import`, `pgos_apply_import`

Context Pack tools (a bounded, immutable snapshot of selected nodes/edges/evidence for handing
off to an agent session):

`pgos_create_context_pack`, `pgos_list_context_packs`, `pgos_get_context_pack`,
`pgos_materialize_context_pack`, `pgos_delete_context_pack`

Every mutation is attributed and recorded in the same Activity log the UI shows — there is no
separate, invisible "agent" write path. Point any MCP-compatible client at
`http://127.0.0.1:8000/mcp` with header `Authorization: Bearer <token>` (from `pgos-token show`).

## Backup, export, and portability

Two related but distinct mechanisms:

- **Export** (`GET /export`, or the UI's **Download export** button) — a `.zip` snapshot of one
  workspace's canonical data (nodes, edges, canvases/placements, resources, saved views, context
  packs, discovery runs, activity log, and managed attachment bytes) plus a manifest listing
  every entry's exact size and SHA-256 hash. It **excludes** the bearer token, the search index,
  MCP idempotency receipts, the pending file-operation journal, and any `FileReference`'s
  absolute path. Use this to inspect or migrate your data by hand.
- **Backup/restore** (`pgos-backup`) — covers the whole SQLite database plus verified managed
  attachment bytes, for disaster recovery. `restore` only ever writes into an **empty**
  destination directory — it will never overwrite an existing workspace. `verify` checks an
  archive's integrity without touching anything.

## Remote access

By default, the server only answers on `127.0.0.1` and is meant to be used from the same
machine. To reach it from another device, put a **same-host HTTPS reverse proxy** in front of it
— see the full runbook at [`docs/runbooks/remote-access.md`](docs/runbooks/remote-access.md) for
exactly what the proxy must (and must never) do: DNS/`PGOS_TRUSTED_HOSTS` setup, certificate
ownership, header handling (never log `Authorization`), streaming/timeout requirements for
`/mcp` and large attachment/export transfers, rotation, and an incident-response checklist.

**Never** bind the backend itself to a non-loopback address, forward its port directly, or put
the token in a URL/query string/build artifact.

## Security model

- **Every** route (including reads) requires `Authorization: Bearer <token>` — there is no
  unauthenticated surface, even for a loopback-only process.
- The token is generated on first run, stored as a `0600` file next to the database, and never
  printed by the server itself, logged, or embedded in the frontend build.
- The frontend never persists the token in a cookie or Web Storage: it's held in memory until
  verified, then committed to the current browser tab's own `history.state`. A new tab (even one
  opened from an already-unlocked tab) always starts locked.
- `PGOS_TRUSTED_HOSTS` rejects any request with an unrecognized `Host` header with `400`,
  independent of whatever a front-end proxy does.
- Export/backup formats explicitly exclude the token, absolute file-reference paths, and other
  rebuildable internals (search index, idempotency receipts).
- The desktop PWA's offline cache holds only the public app shell (JS/CSS/HTML/manifest/icons)
  — it has no route for API, `/mcp`, `/export`, or attachment traffic, so it can never serve
  stale authenticated data or queue a mutation while offline.

## Development

```bash
# Backend
uv sync                              # install/update dependencies
uv run pytest -q                     # default test suite (fast; excludes @pytest.mark.slow)
uv run pytest -q -m slow tests/acceptance   # opt-in slow acceptance suite
uv run ruff check .                  # lint
uv run ruff format .                 # format
uv run pyright                       # type-check

# Frontend (run from frontend/)
npm run dev                          # Vite dev server with HMR
npm run build                        # tsc -b && vite build (production build under dist/)
npm test                             # Vitest unit/component tests
npm run typecheck                    # tsc -b --noEmit
npm run lint                         # oxlint
npm run test:e2e                     # Playwright acceptance tests (build the frontend first)
```

Project conventions: descriptive English identifiers, interface-driven design, no ORM
(hand-written SQLite access behind narrow repository ports), and a strict dependency direction —
`domain` and `application` never import a concrete adapter (SQLite, MCP SDK, FastAPI); adapters
depend inward, never the reverse (enforced by `tests/test_dependency_direction.py`).

## Testing and the release gate

Beyond the standard backend/frontend test suites above, `scripts/system_pilot.py` is the one
documented local command that builds the real frontend, spawns the real packaged server against
a fresh temporary workspace, and runs a full acceptance phase against it — always cleaning up
afterward, success or failure:

```bash
uv run python scripts/system_pilot.py --phase functional       # ST-09.1: seeds a small fixture
                                                                 # covering every product invariant
                                                                 # through real REST/MCP calls
uv run python scripts/system_pilot.py --phase accessibility     # ST-09.2: real-browser axe scan,
                                                                 # keyboard-only acceptance
uv run python scripts/system_pilot.py --phase recovery          # ST-09.4: backup/restore drill +
                                                                 # full UI pilot journey (custom
                                                                 # schema, connect, discovery,
                                                                 # files, undo, export, restart)
uv run python scripts/system_pilot.py --phase performance       # ST-09.3: seeds a representative-
                                                                 # scale dataset, measures p95
                                                                 # backend + browser budgets
```

Each phase writes a sanitized JSON report to `.system-pilot-reports/` (gitignored, no
credentials/paths/titles) and leaves no server process, temporary workspace, or Playwright
result directory behind. See the phase docstrings in `scripts/system_pilot.py` and
[`docs/runbooks/system-pilot.md`](docs/runbooks/system-pilot.md) for full detail.

## Project structure

```
src/personal_graph_os/
  domain/            Pure domain models (Node, Edge, Workspace, schema types, …) — no I/O.
  application/       Use-case services, repository ports (protocols), default schema, discovery,
                     export/backup logic, context packs, activity/undo. Never imports SQLite or
                     the MCP SDK directly.
  infrastructure/
    sqlite/          The only concrete persistence adapter: repositories + migrations.
    mcp/             The only MCP SDK adapter: tool schemas, dispatch, auth, telemetry.
    local_file_store.py   Managed attachment byte storage.
  api/               FastAPI app, routers (one module per resource group), auth, credentials CLI.
  backup/            `pgos-backup` service + CLI.

frontend/
  src/
    components/      One React component per view/panel (GraphCanvas, TableView, KanbanView,
                      TimelineView, SearchView, ResearchView, DiscoveryView, SchemaEditor,
                      Inspector, ActivityView, FilesPanel, SavedViewsPanel, …).
    api/              Typed REST client + session/auth handling.
    lib/               Small framework-agnostic helpers (modal focus lifecycle, workflow chain).
  e2e/                Playwright acceptance specs + the system-acceptance-pilot harness scripts.

tests/                Backend test suite, mirroring src/ (domain/application/infrastructure/api),
                       plus tests/acceptance/ for the ST-09 system-acceptance fixtures/harness.

docs/runbooks/        Operational runbooks (remote-access proxy setup, system-pilot harness).
scripts/system_pilot.py   The system-acceptance CLI described above.
```

## Troubleshooting / FAQ

**"I forgot my token."** Run `uv run pgos-token show` — it reads the persisted token file, it
doesn't generate a new one unless none exists yet.

**"I rotated the token but I'm still locked out / my old tab still works."** Rotation only
replaces the token file; a running server keeps the *old* token in memory until it's restarted.
Stop the server, rotate, then restart it — see [CLI reference](#cli-reference).

**"The UI at `/app/` is missing / I only get the API."** No production build was found. Run
`npm run build` under `frontend/` (or set `PGOS_STATIC_DIR` to point at an existing build), then
restart the backend.

**"I want two independent workspaces on one machine."** Run two server instances with distinct
`PGOS_DB_PATH` and `PGOS_PORT` values; each gets its own database and token file.

**"Can I edit the SQLite file directly?"** Nothing stops you, but it's unsupported — the schema
and every invariant this app relies on are enforced in the application layer, not the database
schema alone. Use the REST API, the UI, or MCP tools instead.

**"Does this call out to the internet?"** No. Discovery only imports candidates you supply
yourself (it does not fetch URLs or search the web); there is no analytics, telemetry, or update
check beyond the PWA's own same-origin service-worker update flow.

## Roadmap / non-goals

Explicitly out of scope for this MVP (see `.ai/work/active/EP-2026-002-personal-graph-os-mvp/WORK.md`
in the internal planning workspace for full detail, if you have access to it):

- Hosted sync, multi-user accounts/RBAC, embeddings/RAG, or built-in LLM calls.
- Arbitrary SQL/filter expressions or a generic dashboard builder.
- Mobile/tablet layout or touch/coarse-pointer-specific interaction design.
- Public-internet hosting, direct non-loopback binding, or built-in TLS/certificate automation
  (see [Remote access](#remote-access) for the supported proxy pattern instead).
- Redo (undo only adds a compensating event forward), arbitrary historical-state editing, or
  cross-device merge/sync.
