# Security Audit Report: ragent Repository
**Date:** 2026-05-22  
**Scope:** Comprehensive secret scanning across codebase, configuration, and deployment files

## Executive Summary ✅
**PASS** — No hardcoded credentials, API keys, tokens, or passwords found.

The ragent project demonstrates strong security practices:
- All credentials sourced from environment variables only
- Dynamic token management with automatic refresh
- Comprehensive logging redaction denylist
- Proper separation of secrets from configuration files
- Kubernetes-compliant secret management pattern

---

## Detailed Findings

### 1. Environment Configuration Files ✅
**Status:** SECURE

**Files Scanned:**
- `.env.example` — template only, no real credentials
- `.env.mcp_hub.example` — template only, no real credentials
- Deployment ConfigMap (`deploy/k8s/configmap.yaml`) — only non-secret tunables

**Key Findings:**
- No `.env` file in repository (only `.example` templates)
- ConfigMap properly separates public configuration from secrets
- VaultSecret operator (`deploy/k8s/vaultsecret.yaml`) manages sensitive data:
  - MARIADB_DSN
  - ES_USERNAME, ES_PASSWORD, ES_API_KEY
  - MINIO_SITES (encrypted in transit)
  - AI_*_API_J1_TOKEN (three separate tokens for LLM, Embedding, Rerank)
  - UNPROTECT_APIKEY
  - FEEDBACK_HMAC_SECRET
  - REDIS_BROKER_URL, REDIS_RATELIMIT_URL

### 2. Credential Handling Code ✅
**Status:** SECURE

**Files Scanned:**
- `src/ragent/clients/auth.py` — TokenManager
- `src/ragent/clients/embedding.py` — EmbeddingClient
- `src/ragent/clients/llm.py` — LLMClient
- `src/ragent/clients/rerank.py` — RerankClient
- `src/ragent/clients/unprotect.py` — UnprotectClient
- `src/ragent/storage/minio_registry.py` — MinIO credential management
- `src/ragent/bootstrap/composition.py` — DI root (credential assembly)

**Security Patterns Observed:**
- Credentials passed via constructor parameters, never hardcoded
- Dynamic token acquisition via `get_token()` callable on each request
- Tokens obtained from environment at boot, not logged or echoed
- TokenManager automatically refreshes J1→J2 before expiry (5-min margin)
- K8s service account token read from secure mount path when enabled
- API URLs passed as configuration, not embedded in code

### 3. Authentication & JWT ✅
**Status:** SECURE

**Files Scanned:**
- `src/ragent/auth/jwt.py` — OIDC/JWKS verification
- `src/ragent/auth/deps.py` — User ID extraction
- `src/ragent/bootstrap/app.py` — JWT header handling

**Key Findings:**
- JWKS fetched once at boot time, cached for request lifetime
- OIDC discovery validates the issuer and TLS certificates
- JWT claim extraction uses secure joserfc library (actively maintained)
- Signature verification enabled against OIDC JWKS
- No hardcoded issuers, audiences, or keys
- OIDC_DOMAIN, OIDC_AUDIENCE, OIDC_USE_HTTPS all from environment

### 4. Logging & Observability ✅
**Status:** SECURE

**File:** `src/ragent/bootstrap/logging_config.py`

**Denylist Applied:**
```
query, prompt, messages, completion, chunks, documents, body,
authorization, cookie, password, token, secret
```

**Coverage:**
- All sensitive fields redacted from structured logs
- Redaction applied to stdlib and structlog uniformly
- Case-insensitive matching for defense-in-depth
- Records marked `content_redacted=True` when scrubbed
- OTEL trace context preserved (no secrets in trace attributes)
- HTTP error logging (`http_logging.py`) redacts auth bodies

### 5. Test Fixtures & Examples ✅
**Status:** SECURE

**Files Scanned:** `tests/conftest.py`, unit and integration test files

**Key Findings:**
- Test fixtures use fake example domains (e.g., `ragent-test.example`)
- In-process fake OIDC server (no external network)
- Mock HTTP transport for JWT/JWKS testing
- Test tokens are truncated/dummy values (`xyz`, `eyJabc`, `secret-j2`)
- MinIO test credentials use standard testcontainers patterns
- No real credentials in test fixtures

### 6. Deployment & Infrastructure ✅
**Status:** SECURE

**Files Scanned:**
- `deploy/k8s/deployment.yaml` — API and worker pods
- `deploy/k8s/vaultsecret.yaml` — Vault integration
- `deploy/k8s/configmap.yaml` — Configuration
- `Dockerfile` — Container image definition

**Key Findings:**
- Deployment uses ConfigMap + SecretRef pattern (Kubernetes best practice)
- Image tag uses `REPLACE_WITH_RELEASE_TAG` placeholder (immutable tags enforced)
- No hardcoded image tags or credentials in manifests
- Vault Secrets Operator keeps K8s secrets in sync
- Least-privilege pod service account binding
- No default credentials or example passwords in Dockerfiles

### 7. API Endpoints ✅
**Status:** SECURE

**Routers Scanned:**
- `src/ragent/routers/chat.py` — Chat completion
- `src/ragent/routers/ingest.py` — Document ingestion
- `src/ragent/routers/retrieve.py` — Retrieval
- `src/ragent/routers/admin_embedding.py` — Embedding management
- `src/ragent/routers/feedback.py` — Feedback collection

**Key Findings:**
- No credentials in API routes or handlers
- Auth extracted from headers/scopes, not hardcoded
- User ID from request scope (middleware-injected)
- All third-party API calls use injected clients with dynamic tokens
- Error responses do not leak internal details or token information

### 8. Documentation ✅
**Status:** SECURE

**Files Scanned:**
- `docs/00_rule.md` — Security rules (includes logging policy)
- `docs/00_spec.md` — Technical specification
- `docs/00_journal.md` — Incident logs and lessons learned
- `README.md` — Quick start guide
- `CLAUDE.md` — Development guidelines

**Key Findings:**
- No example credentials in documentation
- Security rules documented in `00_rule.md` (Logging section)
- Vault/K8s secret management patterns documented
- No real API keys or tokens in any markdown files
- Example configuration uses `example.com` and placeholder values

---

## Vulnerability Checklist

| Category | Status | Evidence |
|----------|--------|----------|
| Hardcoded credentials | ✅ NONE | Environment variables only |
| Hardcoded API URLs | ✅ SECURE | URLs from config/env, test URLs use `example.com` |
| Unredacted logging | ✅ SECURE | Denylist in logging_config.py |
| JWT secrets exposed | ✅ SECURE | JWKS cached, OIDC domain from env |
| Database credentials | ✅ SECURE | MARIADB_DSN from Vault |
| API tokens | ✅ SECURE | Three J1 tokens managed by TokenManager |
| MinIO secrets | ✅ SECURE | access_key, secret_key from MINIO_SITES env var |
| Test fixtures | ✅ SECURE | Mock data, fake domains only |
| Deployment manifests | ✅ SECURE | ConfigMap + VaultSecret pattern |
| Comments/docs | ✅ SECURE | No real credentials in codebase docs |

---

## Recommendations

### Continue
1. **Environment-based credential sourcing** — maintain pattern of env vars only
2. **TokenManager architecture** — dynamic token refresh is secure
3. **Logging redaction** — denylist covers sensitive fields well
4. **Vault integration** — K8s VaultSecret operator is industry-standard

### Consider (Optional)
1. **Audit logging** — consider adding audit trail for credential reads (if compliance requires)
2. **Token rotation policy** — document min/max token lifetime expectations
3. **Secret rotation runbook** — document procedure for rotating API keys without downtime

### Monitor
- Ensure Vault KV-v2 audit logs are enabled in production
- Verify K8s RBAC limiting access to the `ragent` namespace Secret resources
- Check that pod containers run as non-root (enforce via PodSecurityPolicy)

---

## Compliance

**Meets:**
- ✅ OWASP Top 10 A07:2021 (Identification & Authentication Failures) — no hardcoded credentials
- ✅ OWASP Top 10 A02:2021 (Cryptographic Failures) — secrets in Vault, not files
- ✅ CWE-798 (Use of Hard-Coded Credentials) — environment variables only
- ✅ CWE-798-related (Exposed credentials in logs) — denylist redaction
- ✅ Kubernetes best practices — SecretRef + ConfigMap pattern

---

## Conclusion

The ragent project **passes security audit** with no hardcoded secrets, proper credential management, and strong logging controls. The architecture follows industry best practices for credential handling in containerized environments.

**Risk Level:** 🟢 **LOW** (assuming Vault and Kubernetes RBAC are secured)
