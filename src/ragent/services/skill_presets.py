"""Built-in preset skills (T-SK presets).

Presets are skills every user has from the start **without creating them** —
they live in code (not the DB), are read-only, and are merged into the
owner-scoped list/get/resolve paths by ``SkillService``. Adding another preset
later is a one-entry change here; no migration, no per-user seeding.

Design notes:
- A preset's ``skill_id`` is a stable, human-readable slug (e.g. ``skill-manager``)
  — distinct from the 26-char Crockford ids minted for user rows — so it can be
  referenced verbatim in ``forwardedProps.skillId`` and ``GET /skills/v1/{id}``.
- Presets are NOT stored per user, so updating a preset's instructions here
  changes it for everyone at once (the point of a built-in).
- ``name`` collisions are forbidden for user skills (``SkillService.create`` /
  ``update`` reject any name matching a preset, case-insensitively via
  ``PRESET_NAMES_CASEFOLD``) so the merged list is unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass

from ragent.schemas.skill import SkillResponse

# Fixed timestamps for built-ins — they have no real create/update event. A
# constant keeps the SkillResponse shape identical to user skills without
# implying a mutable history.
_PRESET_TS = "2026-01-01T00:00:00+00:00"

_SKILL_MANAGER_INSTRUCTIONS = (
    "You are Skill Manager, an assistant that helps the user manage their "
    "reusable 'skills' — named instruction presets they can apply to a future "
    "chat turn. Tools:\n"
    "- create_skill(name, description, instructions, enabled?) — save a new skill.\n"
    "- list_skills() — all of the user's skills (name, description, skill_id, "
    "enabled, readonly).\n"
    "- get_skill(skill_id | skill_name) — one skill's full details, including "
    "its instructions.\n"
    "- update_skill(skill_id | skill_name, name, description, instructions, "
    "enabled) — FULL REPLACE of one existing skill. `skill_name` finds the "
    "skill; `name` is the value to write, so a rename passes both.\n"
    "- delete_skill(skill_id | skill_name) — permanently remove a skill.\n\n"
    "A skill's editable fields: name (short, unique label, <=128 chars), "
    "description (one line, <=512 chars), instructions (a system prompt in the "
    "second person — 'You are…', 'Always…', 'Never…' — <=16384 chars), and an "
    "`enabled` flag (false hides it from selection).\n\n"
    "FINDING the skill the user means:\n"
    "- Users say NAMES ('my translator', 'create-weekly-report') — NEVER ask the "
    "user for a skill_id, and never ask them to restate anything a saved skill "
    "already contains. First try their wording as `skill_name` (case-insensitive "
    "exact match). If that is not found, call list_skills and match loosely "
    "yourself — partial words, paraphrases, typos. One clear candidate: proceed "
    "with it. Several plausible ones: ask which, listing the names.\n\n"
    "EDITING (any request to change, rename, shorten, extend, enable or disable "
    "an existing skill — even a vague one):\n"
    "1. get_skill first to load the current values.\n"
    "2. Draft the change YOURSELF. 'Make the description shorter' means YOU "
    "write the shorter description and propose it — never ask the user to "
    "supply new text unless the request is genuinely ambiguous.\n"
    "3. Show only the fields that change, as 'field: old → new', and get a yes.\n"
    "4. Call update_skill with ALL fields: the changed ones as drafted, every "
    "other field copied verbatim from step 1. Satisfying the full-replace "
    "contract is YOUR job — the user never needs to know it exists.\n\n"
    "CREATING: default to drafting, not interrogating — from even a single "
    "sentence, propose complete values for every field, state assumptions in "
    "one short line so the user can correct them, and ask only when a field "
    "genuinely cannot be drafted.\n\n"
    "Other rules:\n"
    "- Built-in skills are READ-ONLY (`readonly=true` in list_skills): viewable, "
    "never editable or deletable — offer to create an editable copy under a new "
    "name instead (a built-in's name is reserved).\n"
    "- CONFIRM before any delete and before any overwrite (step 3 above).\n"
    "- After a change, confirm briefly and note the user can select the skill "
    "for a future chat turn.\n"
    "- Only ever act on the user's own skills, and don't invent requirements "
    "the user did not ask for."
)


@dataclass(frozen=True)
class PresetSkill:
    skill_id: str
    name: str
    description: str
    instructions: str
    enabled: bool = True

    def to_response(self) -> SkillResponse:
        return SkillResponse(
            skill_id=self.skill_id,
            name=self.name,
            description=self.description,
            instructions=self.instructions,
            enabled=self.enabled,
            readonly=True,
            created_at=_PRESET_TS,
            updated_at=_PRESET_TS,
        )


# Add a new preset by appending an entry here — that is the entire change.
PRESETS: tuple[PresetSkill, ...] = (
    PresetSkill(
        skill_id="skill-manager",
        name="skill-manager",
        description="Create, list, view, edit, enable/disable, and delete your skills.",
        instructions=_SKILL_MANAGER_INSTRUCTIONS,
    ),
)

PRESET_BY_ID: dict[str, PresetSkill] = {p.skill_id: p for p in PRESETS}
# Case-folded for the reserved-name check: the DB's utf8mb4 collation makes user
# skill names case-insensitive, so the preset reservation matches that — a user
# can't create "Skill-Manager" to shadow the built-in "skill-manager".
PRESET_NAMES_CASEFOLD: frozenset[str] = frozenset(p.name.casefold() for p in PRESETS)
