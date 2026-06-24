# Skills — Frontend Implementation Plan (mco-clean / TWP)

> Backend: ragent `/skills/v1` CRUD + `/chatagent/v3` skill injection (track T-SK).
> This document is the **plan only** for the mco-clean frontend (track T-SK.FE1).
> Scope: let a user manage their own skill presets and attach one to a chat turn.

## 0. Contract recap (what the backend gives us)

| Method | Path | Body / Params | Returns |
|---|---|---|---|
| POST | `/skills/v1` | `{name, description?, instructions, enabled?}` | `201` skill object |
| GET | `/skills/v1` | — | `{ skills: Skill[] }` (newest first) |
| GET | `/skills/v1/{skill_id}` | — | skill / `404 SKILL_NOT_FOUND` |
| PUT | `/skills/v1/{skill_id}` | full body (same as POST) | updated skill |
| DELETE | `/skills/v1/{skill_id}` | — | `204` |

`Skill = { skill_id, name, description, instructions, enabled, created_at, updated_at }`.

All requests carry the user identity the platform already sends (`X-User-Id` /
JWT). Skills are **owner-scoped server-side** — the client never sends an owner,
and a foreign id is a 404. Errors are RFC 9457 `problem+json` with `error_code`
∈ `SKILL_NOT_FOUND | SKILL_NAME_CONFLICT | SKILL_VALIDATION | MISSING_USER_ID`.

**Using a skill in chat:** add `forwardedProps.skillId` to the twp-ai
`RunAgentInput` sent to `/chatagent/v3`. A bad/disabled id comes back as a
`RUN_ERROR` event (`code: "SKILL_NOT_FOUND"`) on the stream — handle it like any
other run error, plus a "this skill is unavailable" hint.

## 1. Where it lives (architecture)

Two surfaces, both inside the existing host shell (`src/`):

1. **Skills management** — a settings-style page. Recommend a **local app** (not
   a remote MF app) under `src/routes/` so it ships with the shell and needs no
   separate deploy: route `/settings/skills` (or a global app `skills`). Reuses
   `@twp/ui` (Radix + Tailwind) for the list/form.
2. **Skill picker** — a small control in the chat composer of the AI chat app
   (the one driving `/chatagent/v3` via `@twp/ai`). Selecting a skill sets the
   active `skillId`, which the data layer attaches to the next run.

Rationale: management is low-traffic CRUD that belongs to the shell; the picker
must live next to the chat input where the run is dispatched.

## 2. Data layer

- **API client** — add a `skills` client. Cleanest home is **`@twp/ai`** (it
  already owns the chat transport + auth token wiring), exposing:
  `listSkills()`, `getSkill(id)`, `createSkill(input)`, `updateSkill(id, input)`,
  `deleteSkill(id)`. Reuse the package's existing fetch wrapper so the
  `access_token` / `X-User-Id` headers and base URL are applied uniformly; map
  non-2xx `problem+json` to a typed `SkillError(error_code, status)`.
- **State (Jotai)** — in the host store:
  - `skillsAtom` — `Skill[]` (loaded on management mount; refetched after writes).
  - `activeSkillIdAtom` — `string | null`, the picker selection. Persist to
    `localStorage` per user+thread (optional) so a reload keeps the choice.
  - Derived `activeSkillAtom` — looks up `activeSkillId` in `skillsAtom`.
- **Run wiring** — where `@twp/ai` builds `RunAgentInput` (e.g. `useTwpAiChat`),
  read `activeSkillIdAtom` and set `forwardedProps = { ...existing, skillId }`
  when non-null. One change, in the run-input builder — nothing else in the
  stream path changes.

## 3. UI components (`@twp/ui` + app code)

- `SkillList` — table/cards: name, description, enabled toggle, edit/delete.
  Empty state → "Create your first skill".
- `SkillForm` (create + edit) — `name` (≤128), `description` (≤512),
  `instructions` (multiline, ≤16384), `enabled` switch. Inline validation
  mirrors backend bounds; on submit map `409 SKILL_NAME_CONFLICT` → field error
  on `name`, `422 SKILL_VALIDATION` → field errors from `errors[]`.
- `DeleteSkillDialog` — confirm; optimistic remove with rollback on failure.
- `SkillPicker` (composer) — dropdown of **enabled** skills + "None"; shows the
  active skill as a chip on the input; clearable. Disabled skills are hidden
  from the picker (they 404 on resolve) but still visible/toggleable in
  management.

## 4. Behaviours / edge cases

- **Isolation is server-enforced** — the UI never filters by user; it just renders
  what `/skills/v1` returns. No owner field anywhere in the client.
- **Conflict** — duplicate name → surface on the `name` field, don't toast-and-lose
  the form.
- **Run-time skill error** — `RUN_ERROR { code: "SKILL_NOT_FOUND" }` → toast
  "Selected skill is no longer available", clear `activeSkillId`, let the user
  resend. (Happens if the skill was deleted/disabled in another tab.)
- **Auth** — reuse the existing token lifecycle (`accessTokenAtom`); no new env
  vars. If `MISSING_USER_ID` ever returns, treat as "not signed in".

## 5. Testing

- **Unit (`packages/ai`)** — skills client: header injection, `problem+json` →
  `SkillError` mapping, each verb's URL/method/body.
- **Unit (state)** — `activeSkillId` → `forwardedProps.skillId` on the built run
  input; null → no `skillId`.
- **Component** — `SkillForm` validation + conflict mapping; `SkillPicker` lists
  only enabled skills and clears on `SKILL_NOT_FOUND`.
- **Shell/e2e (optional)** — create → appears in picker → send a chat turn →
  assert the run input carried `forwardedProps.skillId`.

## 6. Milestones

1. **M1 — data layer**: skills client in `@twp/ai` + atoms + run-input wiring (no UI yet); unit tests green.
2. **M2 — management UI**: route + `SkillList` + `SkillForm` + delete; CRUD works end-to-end against ragent.
3. **M3 — picker**: `SkillPicker` in the composer; selection drives `forwardedProps.skillId`; run-error handling.
4. **M4 — polish**: persistence of selection, empty/error states, a11y, component tests.

## 7. Out of scope (this cycle)

Sharing skills between users, org/team skills, versioning/history, importing
skills, and skill marketplace — all require new backend contracts and are not
part of T-SK.
