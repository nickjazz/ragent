# RAGent — HA Verification Runbook

> **Purpose:** Procedure for operators to manually verify that RAGent runs in
> High Availability mode with no single point of failure in the application tier.
> Intended as a pre-production release gate and periodic (quarterly) drill.

---

## System Topology

| Component | HA mechanism | Minimum replicas |
|---|---|---|
| **API** (`ragent.api`) | K8s Deployment, `replicas: 2`, rolling update | 2 |
| **Worker** (`ragent.worker`) | K8s Deployment, `replicas: 2`, stateless | 2 |
| **Reconciler** | K8s CronJob (every 5 min), single-pod, idempotent | 1 (safety net) |
| **Redis** | Redis Sentinel — 1 primary + 2 sentinels, automatic failover | 3 sentinel nodes |
| **MariaDB** | Primary + async replica, manual promotion | 2 |
| **Elasticsearch** | Multi-node cluster (≥ 3 nodes for quorum) | 3 data nodes |
| **MinIO** | Distributed mode (≥ 4 nodes, erasure coding) | 4 nodes |

---

## Redis Sentinel Failover Verification

1. Identify the current Sentinel primary:
   ```bash
   redis-cli -h <sentinel-host> -p 26379 SENTINEL get-master-addr-by-name ragent-redis
   ```
2. Note the primary IP and port.
3. Stop the primary Redis node (simulate failure):
   ```bash
   redis-cli -h <primary-host> -p 6379 DEBUG sleep 60
   ```
4. Wait up to 30 s for Sentinel election (`SENTINEL get-master-addr-by-name` returns a new address).
5. Verify the API is still accepting requests:
   ```bash
   curl -s http://<api-host>:8000/livez | jq .
   ```
   Expected: `{"status": "ok"}` — no 503 / timeout.
6. Restore the original primary (remove the sleep or restart the node).

**Pass criteria:** Sentinel elects a new primary within 30 s; `/livez` returns `ok` throughout.

---

## API Replica Verification

1. Confirm at least 2 API pods are running:
   ```bash
   kubectl -n ragent get pods -l app.kubernetes.io/component=api
   ```
2. Terminate one API pod:
   ```bash
   kubectl -n ragent delete pod <pod-name>
   ```
3. Verify the Service continues to route to the surviving pod:
   ```bash
   for i in $(seq 1 10); do curl -sf http://<svc-host>:8000/livez; done
   ```
   Expected: all 10 requests return `{"status": "ok"}`.
4. Confirm Kubernetes restarts the killed pod within 30 s.

**Pass criteria:** Zero request failures during pod termination; pod restarts automatically.

---

## Worker Redundancy Verification

1. Confirm at least 2 worker pods are running:
   ```bash
   kubectl -n ragent get pods -l app.kubernetes.io/component=worker
   ```
2. Submit an ingest job:
   ```bash
   curl -X POST http://<api-host>:8000/ingest/v1 \
     -H 'X-User-Id: ops-test' \
     -H 'Content-Type: application/json' \
     -d '{"ingest_type":"inline","content":"HA test document","source_title":"HA test","mime_type":"text/plain"}'
   ```
   Note the `document_id`.
3. Terminate one worker pod while the job is in flight:
   ```bash
   kubectl -n ragent delete pod <worker-pod>
   ```
4. Poll the document status until READY or FAILED (≤ 5 min):
   ```bash
   until kubectl exec -n ragent deploy/ragent-api -- \
     curl -sf http://localhost:8000/ingest/v1/<document-id> | jq -r .status \
     | grep -q 'READY\|FAILED'; do sleep 5; done
   ```
5. Confirm status == READY (surviving worker or reconciler re-dispatched the job).

**Pass criteria:** Document reaches READY state within the aggregate pipeline timeout (PIPELINE_TIMEOUT_SECONDS, default 300 s).

---

## SPOF Inventory

| Potential SPOF | Mitigation | Accepted risk |
|---|---|---|
| Reconciler (K8s CronJob) | Idempotent + 5-min schedule; misses healed on next tick | Up to 5-min delay on PENDING recovery |
| MariaDB replication lag | Async replica; writes always go to primary | Replica may be seconds behind primary |
| Redis Sentinel election | 30 s election window; TaskIQ retries on connection failure | Up to 30 s task-dispatch delay during failover |
| MinIO node failure | Erasure coding tolerates loss of up to N/2 nodes | Reduced write throughput during rebuild |
| External AI APIs (LLM/Embedding/Rerank) | Reranker fail-open (P2.3); embedder and LLM have no fail-open — ingest/chat fail if unreachable | Documents FAILED if embedding is down; chat returns 502 if LLM is down |

---

## Monitoring Checklist (post-deploy)

Verify all panels in the Grafana **RAGent System Overview** dashboard (`deploy/grafana/ragent_overview.json`) show expected values:

- **Ingest Pipeline Rate**: non-zero if test ingest ran
- **Ingest Failure Rate**: < 5 % (green threshold)
- **Worker Pipeline Duration (p99)**: < 120 s (yellow threshold)
- **Reranker Degradation Rate**: 0 (no fail-open events)
- **Reconciler Health**: tick rate > 0 (CronJob running)
- **Readyz Probe Status**: all probes OK (green)

Verify no alerts are firing in the Prometheus Alertmanager for the `ragent.*` rule groups.

---

*Last updated: 2026-05-24 · Owner: SRE · Cadence: quarterly drill or after any infra topology change.*
