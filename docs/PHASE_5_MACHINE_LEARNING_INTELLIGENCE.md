# Phase 5 — Machine Learning Intelligence

## Boundary

Phase 5 provides historical anomaly discovery and behavioral clustering only. It does not optimize routes, recommend an MT, predict the next assignment, override compatibility, label dispatcher errors, or change master data.

Every analysis and model is scoped by `depot_id`.

## Readiness

`GET /api/v1/phase5/readiness?depot_id=...` reuses the observed Loading Order assignment analysis shown by Phase 1 for the selected depot and its latest available date. Readiness is true only when at least one assignment was evaluated and `passed_assignment_count == evaluated_assignment_count`; rounded display percentages cannot bypass the exact equality check. Mismatches and data-quality statuses both count as failed assignments.

The full active MT × SPBU matrix has a different purpose: it identifies eligible fleet opportunity for Engine A. Ineligible cells are expected operational exclusions, not Master Tag Compatibility issues, and do not block readiness. Its Vehicle Class rule follows Phase 1 (`MT capacity <= SPBU maximum`). When requested internally, the response retains this non-blocking matrix summary separately as `master_eligibility_matrix` and the eligible MT IDs by SPBU.

The readiness response retains the Phase 1 date scope, rule source, assignment numerator/denominator, mismatch count, data-issue count, and a scoped drill-down URL. Engine A, dataset preparation, training, model save, and model activation repeat the server-side gate so stale browser state cannot bypass it.

## Engine A

Engine A uses a Baseline Period and no train/test split. It imports the Phase 4 observation preparation functions. The exact deduplication key is:

```text
(depot_id, shipment_id, spbu_id, mt_id)
```

For each observed SPBU it calculates:

- compatible MT count from the readiness compatibility matrix;
- distinct historically used compatible MT;
- utilization breadth = used / compatible;
- dominant MT and share;
- HHI = sum of squared MT shares;
- Shannon entropy and entropy divided by `log(observed MT count)`;
- distinct shipment observations.

Profiles below the configured minimum remain visible as `INSUFFICIENT_DATA` but are excluded from Isolation Forest. Eligible features are standardized. Raw severity is negative `score_samples`, making larger values more unusual. The persisted 0–100 value is within-run min-max scaling; equal raw scores map to zero.

Classification thresholds have one backend source in `phase5_constants.py` and are snapshotted in each run. Peer context uses `floor(log2(compatible_mt_count))` bands and falls back to all sufficient profiles only if a band is empty.

## Engine B dataset

Preparation is explicit and persisted as `MLTrainingRun` with status `DATASET_READY`.

- Tag group: typed multi-hot tags; Vehicle Class is a separately bounded ordinal feature scaled by the maximum training value.
- Shift group: Phase 2 `departure_datetime_used` and full shift distribution. The exact validated shift definition is snapshotted.
- Pairing group: Phase 3 same-shipment membership and pair metrics. Symmetric graph weight is the mean of `P(B|A)` and `P(A|B)`.
- Geographic group: canonical Master SPBU coordinates are validated, then Haversine KNN produces nearest distance, average/median K-nearest distance, and local density. Coordinate state is `VALID`, `MISSING`, or `INVALID`; duplicate coordinates are flagged. Missing/invalid feature values use core-training medians plus an explicit missing indicator.

Every active SPBU receives a deterministic 0–100 sufficiency score from shipment count, operating days, period coverage, shift coverage, pairing evidence, and recency. Central defaults classify `SUFFICIENT >= 80`, `MARGINAL >= 50`, and the remainder `INSUFFICIENT`. Only SUFFICIENT SPBUs fit scalers, UMAP, and HDBSCAN. MARGINAL SPBUs may be transformed and projected after core training; low-confidence projection stays unassigned. INSUFFICIENT SPBUs remain unassigned and never appear as HDBSCAN noise. Inactive historical SPBUs remain excluded.

## Engine B training

The reproducible pipeline is:

```text
weighted Phase 3 graph → seeded Node2Vec walks → PPMI → Truncated SVD
typed tag vector ────────────────┐
full shift distribution ─────────┤
pairing embedding ────────────────┼→ group scaling + sqrt(weight / dimension) → UMAP → HDBSCAN
Haversine KNN proximity ──────────┘
```

Default feature weights are Tag 0.30, Shift 0.20, Pairing 0.30, and Geographic 0.20. Geography can be disabled; its weight must then be zero and the remaining groups must sum to 1.00.

Node2Vec transitions and UMAP receive explicit seeds. The second-order `p`/`q` walk semantics remain intact, while the walk contexts are converted to a positive-PMI matrix and reduced with deterministic `sklearn.decomposition.TruncatedSVD`. This removes the Gensim native Word2Vec extension that could terminate an ARM64 API process with `Illegal instruction`. The Docker runtime also targets Numba's generic CPU profile so UMAP/PyNNDescent does not JIT instructions unsupported by the container CPU. Isolated nodes receive a zero pairing vector. If the graph has no edges, every pairing vector is zero and training continues with a visible warning. The implementation uses the maintained `sklearn.cluster.HDBSCAN`; HDBSCAN noise is never reassigned.

A separate seeded 2D UMAP creates visualization coordinates. The internal UMAP dimension may be higher. Core profiles and behavioral statistics use `CORE_MEMBER` only and report projected marginal coverage separately. `CORE_NOISE` is a valid sufficient-data result. `MARGINAL_PROJECTED` is coverage, not core membership. `INSUFFICIENT_UNASSIGNED` has no embedding coordinate, cluster ID, or fake probability.

The maintained `sklearn.cluster.HDBSCAN` implementation does not expose approximate prediction. Phase 5 therefore uses the fitted UMAP transform followed by nearest core-cluster centroid distance in the internal embedding. Confidence is an exponential function of distance divided by the persisted cluster scale and multiplier. The method, scales, multiplier, and minimum confidence are part of the reproducibility package; projection below threshold becomes `MARGINAL_UNASSIGNED`.

The UI presents the UMAP behavioral-similarity map separately from an OpenStreetMap geographic view. UMAP defaults to core points and can overlay marginal projections; insufficient records are never rendered as embedded members. A saved model reuses its immutable training-run SPBU coordinate snapshot, so later master edits cannot silently change the spatial feature interpretation. The geographic view can still show any valid physical SPBU position with sufficiency and assignment status. A larger dark marker with a yellow outline shows current Master Depot coordinates. Hover shows SPBU name/code, cluster, sufficiency, assignment type, dominant shift, Vehicle Class, and typed tags. Records without valid coordinates remain in the model/table with an explicit count and status.

Geographic Proximity in Phase 5 is a clustering feature only. It uses coordinate relationships and Haversine distance; it does not represent road distance, large-vehicle feasibility, travel time, traffic, or route optimization.

Training produces a review result, not a registry model. Saving requires a non-empty name.

## Artifact and registry lifecycle

On training, a joblib bundle is written to `ML_ARTIFACT_DIR/staging`. Saving creates:

```text
ML_ARTIFACT_DIR/{model_id}/v{version}/model.joblib
ML_ARTIFACT_DIR/{model_id}/v{version}/manifest.json
```

The bundle contains preprocessing metadata, embeddings, feature vectors, both UMAP models, HDBSCAN, assignments, and profiles. `ml_model_artifact` stores only relative URI, checksum, and size.

Versions never overwrite. Activate demotes the previous active depot model to `SAVED`. Active models cannot be deleted. Duplicate returns configuration only. Archived packages remain reproducible.

The Behavioral Clustering workspace can list saved models for the selected depot and open one without preparing a dataset or retraining. The stored UMAP coordinates, assignments, cluster profiles, membership probabilities, model status, training period, and shift-definition snapshot are rendered through the same review panels used for a fresh training result. Save/retrain controls are replaced by registry-detail and close actions while a saved model is open.

## Model comparison

HDBSCAN labels are arbitrary. Comparison builds SUFFICIENT core membership sets, excludes marginal projections and insufficient records, calculates cross-model Jaccard similarity, and applies Hungarian optimal matching. It also reports population/configuration/geographic differences, marginal projection rate/confidence, and per-SPBU data-maturity transitions independently from cluster matching.

## API

- `GET /api/v1/phase5/readiness`
- `POST /api/v1/phase5/engine-a/analyze`
- `GET /api/v1/phase5/engine-a/runs`
- `GET /api/v1/phase5/engine-a/runs/{id}`
- `GET /api/v1/phase5/engine-a/runs/{id}/spbu/{spbu_id}`
- `POST /api/v1/phase5/engine-b/prepare-dataset`
- `POST /api/v1/phase5/engine-b/training-runs/{id}/train`
- `GET /api/v1/phase5/engine-b/training-runs/{id}`
- `POST /api/v1/phase5/engine-b/training-runs/{id}/save`
- `GET /api/v1/phase5/models`
- `GET /api/v1/phase5/models/active`
- `GET /api/v1/phase5/models/{id}`
- `POST /api/v1/phase5/models/{id}/activate`
- `POST /api/v1/phase5/models/{id}/duplicate`
- `POST /api/v1/phase5/models/{id}/status`
- `DELETE /api/v1/phase5/models/{id}`
- `POST /api/v1/phase5/models/compare`

## Operational limitations

- Phase 5 training masih synchronous di proses API; durable worker queue yang tersedia saat ini khusus Phase 6 prediction. Persisted training states dan friendly errors tetap menjadi seam untuk migrasi worker Phase 5, tetapi depot sangat besar harus memakai API timeout yang sesuai workload ML.
- API startup marks any stale `PREPARING_DATA`, `TRAINING`, or `CALCULATING_PROFILES` run as `FAILED`, retaining the diagnostic and allowing a complete retained dataset to be retried. Docker Compose also restarts the API after an unexpected process exit.
- The existing repository has no login provider. Header-based permission hooks are an integration seam, not production authentication.
- Node2Vec embeddings describe the training-period graph. Isolated/no-edge fallback deliberately contributes no pairing signal.
- The active-model interface serves saved assignments/profiles and explicit evidence coverage to later phases. Phase 6 Data Demo uses only active, non-noise assignments with `coverage_source=BEHAVIORAL_HISTORY` and `history_eligible=true`.
