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

Phase 6 extends the same modular monolith through separate validation, inference, assignment, persistence/service, export, route, and authorization modules. Phase 5 artifact integrity is checked before inference when a joblib package exists; normalized model-registry assignments provide the persisted representation for legacy development databases. Phase 4 affinity creates ranking evidence, while `compatibility.evaluate_compatibility_entities` remains the only master-rule evaluator. NetworkX maximum-weight matching solves the one-to-one assignment globally per shift on the backend. The browser never performs combinatorial assignment.

Phase 6 run rows retain immutable input/model/parameter/original-result snapshots. Current final shipment/assignment rows can change through audited overrides without rewriting the original model layer. `phase6_auth` exposes `phase6:view`, `phase6:run`, `phase6:export`, and `phase6:override` through the same replaceable header seam. Phase 6 has no dependency on a Phase 7 service and contains no route, distance, travel-time, or VRP solver.
