# ragent

RAG backend — ingest, hybrid retrieval, chat.

---

## Quick Start

Prerequisites: Python ≥ 3.12, `uv`, MariaDB 10.6, Redis (Sentinel), Elasticsearch 9.2.3, MinIO.

```bash
uv sync                                                  # install dependencies
cp .env.example .env                                     # then edit .env to fill in DSNs, MinIO sites, API URLs
make doctor                                              # pre-flight check (env + datastores + AI endpoints)
uv run --env-file .env alembic upgrade head              # run database migrations
uv run --env-file .env python -m ragent.api              # start the API server (port 8000)
uv run --env-file .env python -m ragent.worker           # start the background worker (separate shell)
curl http://localhost:8000/livez                         # verify — expect {"status":"ok"}
make doctor PROBE_LIVE=1                                 # post-launch — also probes /livez and /readyz
```

### MCP Hub

Standalone FastMCP service that turns REST endpoints declared in yaml into
MCP tools. Independent from the `POST /mcp/v1` JSON-RPC server inside the API
(which only exposes `retrieve`); Hub federates many third-party APIs into one MCP surface.

```bash
# 1. Point MCP_HUB_TOOLS_YAML at a single yaml or a directory of yamls
#    (one file per upstream system; tool names auto-qualified as <system>.<tool>).
export MCP_HUB_TOOLS_YAML=src/ragent/mcp_hub/tools.example.d   # demo registry

# 2. Validate the registry (fails fast on bad yaml, dup names, dangling path params).
uv run python -m ragent.mcp_hub.doctor

# 3. Run.
uv run python -m ragent.mcp_hub.server                         # binds 0.0.0.0:9000/mcp
```

### Development

```bash
make check        # format + lint + test (Linux / macOS)
make test         # pytest with 92% coverage gate
make format       # ruff format
make lint         # ruff check --fix
```

Windows (run targets individually via `uv`):

```powershell
uv run ruff format .
uv run ruff check . --fix
uv run pytest --cov=src/ragent --cov-branch --cov-fail-under=92
```

#### PDF OCR (Windows)

PDF ingest uses PyMuPDF for text extraction and Tesseract for image-bearing pages (OCR).
Tesseract must be installed at the OS level:

```powershell
winget install UB-Mannheim.TesseractOCR
# Re-open your terminal, then verify:
tesseract --version
```

Add the Tesseract install directory (e.g. `C:\Program Files\Tesseract-OCR`) to `PATH` and set
`TESSDATA_PREFIX` to its `tessdata` subfolder if PyMuPDF cannot locate language data at runtime.
For CJK / Japanese / German documents, install the matching language packs from the same
UB-Mannheim installer (select additional languages during setup).

On Linux and macOS, install via the system package manager (`apt`, `brew`, etc.).
The production `Dockerfile` installs `tesseract-ocr-eng`, `-chi-sim`, `-chi-tra`, `-jpn`, `-deu`.

---

## Docs

| File | Purpose |
|---|---|
| [`docs/API.md`](docs/API.md) | API reference (ingest, chat, retrieve, observability, MCP) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System diagram and key design decisions |
| [`docs/00_rule.md`](docs/00_rule.md) | Development standards and mandatory workflow |
| [`docs/00_rule_third_party_api.md`](docs/00_rule_third_party_api.md) | Third-Party API request/response field-name samples (split from `00_rule.md`) |
| [`docs/00_spec.md`](docs/00_spec.md) | Full technical specification |
| [`docs/00_plan.md`](docs/00_plan.md) | TDD implementation checklist |
| [`docs/00_agent_team.md`](docs/00_agent_team.md) | Agent team and workflow |
| [`docs/00_journal.md`](docs/00_journal.md) | Team reflection and blameless guidelines |
