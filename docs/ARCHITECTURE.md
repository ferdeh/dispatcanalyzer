# Architecture

The repository is a monorepo:

- `apps/api`: FastAPI, SQLAlchemy models, import pipeline, compatibility API.
- `apps/web`: React, TypeScript, Tailwind, ECharts dashboard.
- `db/migrations`: Alembic migrations targeting PostgreSQL/PostGIS.
- `services/analytics`: idempotent analytics job entry points.
- `example data`: source workbooks used to validate Phase 0.

Phase 0 uses PostgreSQL/PostGIS as the system of record. All imports create an `import_audit` record and staging rows before canonical tables are updated. Canonical tables keep `source_import_id` so published data can be traced back to the source file and sheet.

The backend API is versioned under `/api/v1`. Later phases should add derived fact tables and endpoints without changing Phase 0 canonical identifiers.

Phase 5 remains inside the FastAPI modular monolith. Synchronous status transitions (`PENDING`, `PREPARING_DATA`, `TRAINING`, `CALCULATING_PROFILES`, `COMPLETED`, `FAILED`) preserve an upgrade path to a background worker without adding a second service prematurely. Engine B joblib packages and JSON manifests are stored under `ML_ARTIFACT_DIR`; Docker Compose mounts the `ml_artifacts` named volume. Database rows retain parameters, snapshots, assignments, profiles, relative artifact URI, SHA-256, audit user, and timestamps.

The repository has no authentication provider. Phase 5 exposes a replacement seam through `phase5_auth`: local calls preserve current permissive behavior, while deployments may pass `X-User` and comma-separated `X-Permissions` (`phase5:view`, `phase5:run`, `phase5:train`, `phase5:save`, `phase5:activate`, `phase5:delete`). This is not a login system; production should replace the dependency with the platform identity provider.

Phase 6 extends the same modular monolith through validation, inference, rolling assignment, route estimation, persistence, export, settings, and authorization modules. Phase 5 artifact integrity is checked before inference when a joblib package exists; normalized model-registry assignments remain the persisted representation for legacy development databases. Phase 4 affinity creates ranking evidence, while `compatibility.evaluate_compatibility_entities` remains the single master-rule evaluator.

Phase 6 prediction execution is outside the FastAPI process. The API transaction persists a `prediction_run` and `prediction_job`, then returns `202`; the dedicated `phase6-worker` service claims queue rows with PostgreSQL `FOR UPDATE SKIP LOCKED`. Each attempt receives a lease/fencing token and runs in an isolated child process. The parent worker updates heartbeat/lease metadata, enforces a hard execution timeout, and recovers an expired lease to `QUEUED` until `max_attempts` is reached. Final prediction rows and the job `COMPLETED` state commit atomically after validating the fencing token, preventing an obsolete worker from committing after recovery. API/container restarts therefore do not lose queued work or leave permanent orphan `RUNNING` rows.

```text
Timestamped LO + Initial MT Availability
    → Phase 2 shift derivation
    → Phase 5 time-feasible shipment prediction
    → Phase 4 MT score + Phase 1 compatibility
    → rolling chronological vehicle state
    → DRIVE-only Google/fallback travel estimate
    → cycle time, estimated return, next availability
    → persisted final trip timeline + Phase 7 input
```

`Phase6RouteEstimationService` estimates one predicted shipment at a time. It may evaluate small permutations or nearest-neighbor stop order solely to estimate cycle time. It never calls Google Route Optimization, GMPRO `optimizeTours`, or a fleet-wide VRP solver. That boundary keeps preliminary `estimated_visit_sequence` distinct from the final optimized route owned by Phase 7.

Google Routes requests are backend-only. The global API key is encrypted using an environment-provided application secret; the database stores ciphertext/fingerprint/mask, the frontend receives only the mask, and prediction snapshots retain configuration version—not key material. Phase 6 Indonesia enforces `DRIVE`; no TRUCK/Large Vehicle request or profile is sent. Route cache identity includes endpoints, departure bucket, routing preference, DRIVE mode, and configuration version. Google failures use visibly marked historical/default route estimates.

Phase 6 run rows retain immutable input/model/routing/parameter/original-result snapshots and a separate final dispatch snapshot. Audited overrides recalculate route/cycle/availability and downstream rolling state without retraining Phase 5 or rewriting the original model layer. `phase6_auth` exposes `phase6:view`, `phase6:run`, `phase6:export`, `phase6:override`, `google_routes:view`, and `google_routes:manage` through the same replaceable header seam.
