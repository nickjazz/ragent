# Simplify: Code Review and Cleanup

Review all changed files for reuse, quality, and efficiency. Fix any issues found.

## Mode selection

This skill accepts an optional `--mode fast|full` argument (default: **`fast`**).

| Mode | When to use | What runs |
|------|-------------|-----------|
| `--mode fast` | Standard push gate (default) | Single inline pass — reuse, quality, efficiency in one response |
| `--mode full` | Manual deep review on explicit request | Three parallel sub-agents (Reuse · Quality · Efficiency) |

**Stamp used:** `simplify:fast` (default) or `simplify:full` (see Phase 4).

---

## Phase 1: Identify Changes

Run `git diff` (push context, no staged changes) or `git diff --cached` (commit context, staged changes exist).  
If no git changes in either case, review the most recently modified files mentioned by the user or edited in this conversation.

---

## Phase 2: Review

### fast mode — single-pass inline review

In a single response, scan the diff for:

1. **Reuse**: any new function that duplicates an existing utility; inline logic that could use an existing helper.
2. **Quality**: copy-paste blocks, nested conditionals 3+ levels deep, stringly-typed code, unnecessary comments explaining WHAT (not WHY).
3. **Efficiency**: N+1 patterns, sequential work that could be parallel, unbounded data structures.

List findings concisely (one line each). Fix any that are clear-cut. Skip false positives — note and move on.

### full mode — three parallel sub-agents

> **MANDATORY — no exceptions:** ALWAYS launch all three sub-agents even if the diff appears small, focused, or surgical. The phrase *"diff is small/inline review sufficient"* is a process violation — see journal Process 2026-05-17 "Inline /simplify rationalization". The fan-out **is** the review; skipping it means skipping the review.

Use the Agent tool to launch all three agents concurrently in a single message. Pass each agent the full diff so it has complete context.

#### Agent 1: Code Reuse Review

For each change:

1. **Search for existing utilities and helpers** that could replace newly written code. Look for similar patterns elsewhere in the codebase — common locations are utility directories, shared modules, and files adjacent to the changed ones.
2. **Flag any new function that duplicates existing functionality.** Suggest the existing function to use instead.
3. **Flag any inline logic that could use an existing utility** — hand-rolled string manipulation, manual path handling, custom environment checks, ad-hoc type guards, and similar patterns are common candidates.

#### Agent 2: Code Quality Review

Review the same changes for hacky patterns:

1. **Redundant state**: state that duplicates existing state, cached values that could be derived, observers/effects that could be direct calls
2. **Parameter sprawl**: adding new parameters to a function instead of generalizing or restructuring existing ones
3. **Copy-paste with slight variation**: near-duplicate code blocks that should be unified with a shared abstraction
4. **Leaky abstractions**: exposing internal details that should be encapsulated, or breaking existing abstraction boundaries
5. **Stringly-typed code**: using raw strings where constants, enums (string unions), or branded types already exist in the codebase
6. **Nested conditionals**: ternary chains (`a ? x : b ? y : ...`), nested if/else, or nested switch 3+ levels deep — flatten with early returns, guard clauses, a lookup table, or an if/else-if cascade
7. **Unnecessary comments**: comments explaining WHAT the code does, narrating the change, or referencing the task/caller — delete; keep only non-obvious WHY (hidden constraints, subtle invariants, workarounds)
8. **Bare `MagicMock()` on typed collaborators**: any `MagicMock()` (no `spec=`) used as a stand-in for a real service/client/broker class — should be `MagicMock(spec=RealClass)` or `create_autospec(RealClass)`. Bare mocks accept any attribute and hide `AttributeError` bugs that only surface at runtime. (`docs/00_rule.md` §Test Log Capture)
9. **SQL `split(";")` without strip-then-split**: any code that loads `.sql` text and splits by `";"` without first stripping `--` comments line-by-line — use `iter_statements(sql)` from `ragent.bootstrap.init_schema`. (`docs/00_rule.md` §Database Practices)
10. **Mock return-value type mismatch**: a `return_value`/`side_effect` for a typed method (dataclass, Pydantic model, ORM row) set to a `dict` or bare `MagicMock()` instead of a real instance — `spec=` only verifies the call surface, not the returned shape. (`docs/00_rule.md` §Test Log Capture)

#### Agent 3: Efficiency Review

Review the same changes for efficiency:

1. **Unnecessary work**: redundant computations, repeated file reads, duplicate network/API calls, N+1 patterns
2. **Missed concurrency**: independent operations run sequentially when they could run in parallel
3. **Hot-path bloat**: new blocking work added to startup or per-request/per-render hot paths
4. **Recurring no-op updates**: state/store updates inside polling loops or event handlers that fire unconditionally — add a change-detection guard
5. **Unnecessary existence checks**: pre-checking file/resource existence before operating (TOCTOU anti-pattern) — operate directly and handle the error
6. **Memory**: unbounded data structures, missing cleanup, event listener leaks
7. **Overly broad operations**: reading entire files when only a portion is needed, loading all items when filtering for one

---

## Phase 3: Fix Issues

Aggregate findings and fix each issue directly. If a finding is a false positive, note and skip.

Then run format + lint on all changed `.py` files to prevent a push-gate format failure from forcing a restamp cycle:

```bash
_UP="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
if [[ -n "$_UP" ]] && git diff --cached --quiet 2>/dev/null; then
    _PY=($(git diff "${_UP}...HEAD" --name-only 2>/dev/null | grep '\.py$' || true))
else
    _PY=($(git diff --cached --name-only 2>/dev/null | grep '\.py$' || true))
fi
if [[ ${#_PY[@]} -gt 0 ]]; then
    uv run ruff format "${_PY[@]}"
    uv run ruff check --fix "${_PY[@]}"
    git add "${_PY[@]}"
fi
```

Briefly summarize what was fixed (or confirm the code was already clean).

---

## Phase 4: Stamp (mandatory final step)

Auto-detects push vs commit context; binds stamp to the appropriate diff sha.

```bash
_UP="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
if [[ -n "$_UP" ]] && git diff --cached --quiet 2>/dev/null; then
    _SHA="$(git diff "${_UP}...HEAD" 2>/dev/null | sha256sum | cut -d' ' -f1)"
else
    _SHA="$(git diff --cached 2>/dev/null | sha256sum | cut -d' ' -f1)"
fi

# Default (fast mode):
RAGENT_SKILL_INVOCATION_TOKEN=1 RAGENT_DIFF_SHA="$_SHA" bash .claude/hooks/stamp_pre_commit_approved.sh simplify:fast

# Only when --mode full was explicitly requested:
# RAGENT_SKILL_INVOCATION_TOKEN=1 RAGENT_DIFF_SHA="$_SHA" bash .claude/hooks/stamp_pre_commit_approved.sh simplify:full
```
