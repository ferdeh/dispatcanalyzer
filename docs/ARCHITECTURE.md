# Architecture

The repository is a monorepo:

- `apps/api`: FastAPI, SQLAlchemy models, import pipeline, compatibility API.
- `apps/web`: React, TypeScript, Tailwind, ECharts dashboard.
- `db/migrations`: Alembic migrations targeting PostgreSQL/PostGIS.
- `services/analytics`: idempotent analytics job entry points.
- `example data`: source workbooks used to validate Phase 0.

Phase 0 uses PostgreSQL/PostGIS as the system of record. All imports create an `import_audit` record and staging rows before canonical tables are updated. Canonical tables keep `source_import_id` so published data can be traced back to the source file and sheet.

The backend API is versioned under `/api/v1`. Later phases should add derived fact tables and endpoints without changing Phase 0 canonical identifiers.
