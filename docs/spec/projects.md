# projects — Project grouping over upstream-owned sessions

> Part of [docs/00_spec.md §3.10](../00_spec.md#310-projects). Standard: [docs/00_rule.md](../00_rule.md).

---

## §3.10 `/project/v1` — grouping sessions into projects

### Background — why ragent owns the project mapping

ChatAgent **sessions are owned entirely by the external upstream**, keyed by
`(user, apName, session)`. ragent holds no session content; `/chatagent/v3`
mints the session id (Model B, see [chatagent_v3.md](chatagent_v3.md) §3.4.7) and
proxies `sessionList` / `session` GET/PUT/DELETE to the upstream. The upstream
has **no concept of a project** — its only keys are `user`, `apName`, `session`.

A *project* groups several sessions under one name. Because the upstream cannot
store that grouping, **ragent owns the project entity and the project↔session
membership in MariaDB**, and overlays it on the upstream-owned session content.
Session titles, message history, and timestamps stay in the upstream (the single
source of truth for session *content*); ragent contributes only the *grouping*.

This is a distinct domain from `/chatagent/v3` and may evolve independently.

### Data model (MariaDB)

Two tables (no FK — application-layer relations per `00_domain_map.md §2.4`):

```
projects
  project_id  CHAR(26)     PRIMARY KEY   -- new_id(), §5.3
  user_id     VARCHAR(64)  NOT NULL
  name        VARCHAR(255) NOT NULL
  created_at  DATETIME(6)  NOT NULL
  updated_at  DATETIME(6)  NOT NULL
  INDEX idx_projects_user (user_id)      -- list a user's projects

project_sessions
  session_id  VARCHAR(64)  PRIMARY KEY   -- upstream session id (ragent-minted)
  project_id  CHAR(26)     NOT NULL
  user_id     VARCHAR(64)  NOT NULL
  created_at  DATETIME(6)  NOT NULL
  INDEX idx_project_sessions_project (project_id)  -- list one project's sessions
  INDEX idx_project_sessions_user    (user_id)     -- exclude grouped from sessionList
```

`session_id` is the **PRIMARY KEY**: this is how *"a session belongs to at most
one project"* is enforced at the storage layer. A session with no row is
**ungrouped** — projects are optional. All queries additionally scope by
`user_id` so one user can never read or mutate another user's projects/sessions.

### Endpoints

All routes require `Depends(get_user_id)`; the resolved caller scopes every
query. `projectId` is a 26-char id; `session` is the upstream session id.

| Method | Path | Request | Response |
|---|---|---|---|
| POST   | `/project/v1`                          | `{ "name": str }` | `201 { projectId, name, createTime }` |
| GET    | `/project/v1`                          | — | `200 { projects: [{ projectId, name, createTime, updateTime, sessionCount }] }` |
| GET    | `/project/v1/{projectId}`              | — | `200 { projectId, name, sessions: [SessionEntry] }` (`404 PROJECT_NOT_FOUND`) |
| PUT    | `/project/v1/{projectId}`              | `{ "name": str }` | `200 { projectId, name, updateTime }` (`404 PROJECT_NOT_FOUND`) |
| DELETE | `/project/v1/{projectId}`              | — | `200 { projectId, sessionsDeleted, sessionsFailed: [session] }` (`404 PROJECT_NOT_FOUND`) |
| POST   | `/project/v1/{projectId}/sessions`     | `{ "session": str }` | `204` (`404 PROJECT_NOT_FOUND` / `409 PROJECT_SESSION_CONFLICT`) |
| DELETE | `/project/v1/{projectId}/sessions/{session}` | — | `204` (`404 PROJECT_NOT_FOUND` / `502`/`504` on upstream delete) |

`SessionEntry` mirrors a `/chatagent/v3/sessionList` entry: `{ session,
sessionName, ... }` with the machine-context wrapper stripped from `sessionName`
(same rule as `map_session_list_payload`).

### Listing a project's sessions — membership ∩ upstream

`GET /project/v1/{projectId}` reads the project's `session_id` set from
`project_sessions`, calls the upstream `sessionList` (the whole user's sessions),
and returns the **intersection**, reshaped as `SessionEntry`. The upstream stays
the source of truth for title/time; ragent supplies only the grouping.

A membership row whose session is **absent** from the upstream list (e.g. the
session was deleted upstream out-of-band) is silently dropped from the response
and **lazily removed** from `project_sessions` — it is a dangling row, not a
session.

### Associating a session with a project

Two paths, both *"create membership if absent"*:

1. **At chat time — `POST /chatagent/v3` with `forwardedProps.projectId`.** When
   present, ragent records membership `(minted thread_id → projectId)` right after
   it resolves the session id, before streaming. This is **best-effort and never
   blocks the chat**: if `projectId` is missing, malformed, not owned by the
   caller, or the membership write fails, ragent logs a warning
   (`chatagent_v3.project_associate_failed`) and the session is created ungrouped.
   If the session already has a membership (a continuing thread), it is **left
   unchanged** — chat-time association creates, never moves.
2. **Explicitly — `POST /project/v1/{projectId}/sessions`.** Adds an existing
   (typically ungrouped) session to a project. If the session already belongs to
   **another** project, the `session_id` PRIMARY KEY collides → `409
   PROJECT_SESSION_CONFLICT` (re-adding to the *same* project is idempotent
   `204`). There is no implicit move.

### Deletion semantics — a session's lifecycle follows its project

Removing a session from a project **deletes the session**, not just the link
(decision: 2026-06-25 design session):

- `DELETE /project/v1/{projectId}/sessions/{session}` — calls the upstream
  `DELETE /session` first; **on success**, removes the `project_sessions` row →
  `204`. On upstream failure the membership is **left intact** and the failure
  surfaces as `502 CHATAGENT_UPSTREAM_ERROR` / `504 CHATAGENT_TIMEOUT` (the
  single-session path is atomic-ish: the link is dropped only once the session is
  actually gone).
- `DELETE /project/v1/{projectId}` (whole project) — for each member session,
  calls the upstream `DELETE /session` (best-effort, collecting failures), then
  deletes the `project_sessions` rows and the `projects` row. A session whose
  upstream delete failed becomes an **ungrouped orphan** (recoverable, not lost)
  and is reported in `sessionsFailed`. The bulk path cannot be transactional
  across many upstream calls, so it returns a `200` summary rather than `204`.

Because the upstream is not transactional, neither path holds a DB transaction
across the upstream call (`00_spec.md §3.1` locking rule). There is **no
"unlink without delete"** operation in this cycle; consequently there is no
"move a session between projects" path. If moving is needed later, add an
explicit unlink action — it does not change this design.

### Left-list exclusion — `GET /chatagent/v3/sessionList` hides grouped sessions

The flat session list (the app's left sidebar) must show **only ungrouped
sessions**; sessions that live under a project render inside that project, not
twice. `GET /chatagent/v3/sessionList` therefore subtracts the caller's grouped
set — `SELECT session_id FROM project_sessions WHERE user_id = ?` (one indexed
query) — from the upstream list before applying the existing name-strip transform.

The membership read is injected into the v3 router as a callable
(`grouped_session_ids(user_id) -> set[str]`), built in the composition root from
`ProjectService` — the router never imports the repository or service directly,
matching the existing T-CAv3.DIP injection pattern.

### Errors

| `error_code` | HTTP | When |
|---|---|---|
| `PROJECT_NOT_FOUND` | 404 | `projectId` not found for the caller |
| `PROJECT_SESSION_CONFLICT` | 409 | adding a session already owned by another project |
| `CHATAGENT_UPSTREAM_ERROR` | 502 | upstream `DELETE /session` failed (single-session remove) |
| `CHATAGENT_TIMEOUT` | 504 | upstream `DELETE /session` timed out (single-session remove) |

Both new codes are added to `src/ragent/errors/codes.py` (`HttpErrorCode`) and
[`docs/spec/error_codes.md`](error_codes.md) in the same commit (`00_rule.md`).

### Configuration

No new env vars. The cascade-delete path reuses the existing
`CHATAGENT_SESSION_API_URL` + `CHATAGENT_AP_NAME` (the same upstream the v3
`DELETE /session` proxy uses); the project routes are registered only when that
URL is set (so cascade delete is always possible where projects are enabled).
The repository uses the shared MariaDB engine from the composition root.
