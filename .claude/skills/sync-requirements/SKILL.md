---
name: sync-requirements
description: Delete uv.lock, re-sync with dev extras/group against the custom pip registry, and export a hash-free requirements.txt. Use when you need to refresh the lock file or regenerate requirements.txt for CI.
---

# sync-requirements

Refresh the uv lock file and export a hash-free `requirements.txt` for CI pipelines backed by a private pip registry.

---

## Phase 1: Verify Registry Config

Check that the custom registry is reachable before touching the lock file.

The registry URL is read from the environment. Preferred resolution order:
1. `UV_INDEX_URL` — replaces PyPI as the primary index.
2. `UV_EXTRA_INDEX_URL` — supplements PyPI (use when internal packages co-exist with public ones).
3. `[[tool.uv.index]]` block in `pyproject.toml` (already committed config).

Run:
```bash
echo "UV_INDEX_URL=${UV_INDEX_URL:-<not set>}"
echo "UV_EXTRA_INDEX_URL=${UV_EXTRA_INDEX_URL:-<not set>}"
grep -A3 '^\[\[tool.uv.index\]\]' pyproject.toml || echo "(no [[tool.uv.index]] in pyproject.toml)"
```

If no registry is configured and the project has internal packages (e.g. `twp-ai` from a private source), warn the user and stop — do not proceed with a stale or broken resolution.

---

## Phase 2: Remove Lock File

```bash
rm -f uv.lock
```

Removing the lock forces a full re-resolution from the registry on the next sync. This is intentional.

---

## Phase 3: Sync with Dev Extras and Group

```bash
uv sync --extra dev --group dev
```

- `--extra dev` — installs `[project.optional-dependencies] dev`.
- `--group dev` — installs `[dependency-groups] dev` (pytest, ruff, testcontainers, etc.).
- uv automatically picks up `UV_INDEX_URL` / `UV_EXTRA_INDEX_URL` from the environment; no extra flags needed.

This regenerates `uv.lock` and installs into the local venv.

---

## Phase 4: Export Hash-Free requirements.txt

```bash
uv export \
  --no-hashes \
  --extra dev \
  --group dev \
  --emit-index-url \
  --output-file requirements.txt
```

Flag rationale:
| Flag | Why |
|------|-----|
| `--no-hashes` | Private registries often serve packages without SHA-256 hash metadata; hashes would cause `pip install` to fail on CI. |
| `--extra dev` / `--group dev` | Mirrors Phase 3 — requirements.txt reflects the full dev environment. |
| `--emit-index-url` | Embeds `--index-url <registry>` (or `--extra-index-url`) at the top of the file so CI `pip install -r requirements.txt` needs no extra flags. |
| `--output-file requirements.txt` | Writes to project root. |

If you only need a production requirements file (no dev tooling), drop `--extra dev --group dev`.

---

## Phase 5: Verify

```bash
head -10 requirements.txt
echo "---"
wc -l requirements.txt
```

Confirm:
- The file is non-empty.
- If `--emit-index-url` was used, the first line is `--index-url <your-registry>` (or `--extra-index-url`).
- Key packages from `pyproject.toml` (e.g. `fastapi`, `haystack-ai`) appear in the list.

---

## Notes

- **Re-running on CI**: set `UV_INDEX_URL` (or `UV_EXTRA_INDEX_URL`) as a CI secret, then call this skill or run the four commands above directly in your pipeline step.
- **Workspace package `twp-ai`**: `uv export` will pin it by editable path (`-e packages/twp-ai`). If CI does not have the workspace checked out, replace the editable line with a wheel from your registry.
- **Removing `--emit-index-url`**: safe to drop if the registry URL is already baked into CI via `pip.conf` or a `.pth` file.
