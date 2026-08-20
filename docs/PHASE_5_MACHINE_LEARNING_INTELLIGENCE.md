# Phase 5 — Machine Learning Intelligence

## Boundary

Phase 5 provides historical anomaly discovery and behavioral clustering only. It does not optimize routes, recommend an MT, predict the next assignment, override compatibility, label dispatcher errors, or change master data.

Every analysis and model is scoped by `depot_id`.

## Readiness

`GET /api/v1/phase5/readiness?depot_id=...` batches the active depot master space while calling the same canonical evaluator used by the single-pair compatibility API. Readiness is true only when at least one pair was evaluated and `passed_pair_count == evaluated_pair_count`; rounded display percentages cannot bypass the equality check.

The response retains rule source, compatibility mode, numerator, denominator, failure counts, and issue examples. Engine A, dataset preparation, and training repeat the server-side gate so a stale browser state cannot bypass it.

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

SPBUs below the minimum shipment count are recorded in the dataset exclusion summary.

## Engine B training

The reproducible pipeline is:

```text
weighted Phase 3 graph → Node2Vec
typed tag vector ────────────────┐
full shift distribution ─────────┼→ group scaling + sqrt(weight / dimension) → UMAP → HDBSCAN
pairing embedding ────────────────┘
```

Node2Vec and UMAP receive explicit seeds and Node2Vec/Word2Vec use one worker. Isolated nodes receive a zero pairing vector. If the graph has no edges, every pairing vector is zero and training continues with a visible warning. The implementation uses the maintained `sklearn.cluster.HDBSCAN`, avoiding platform-specific native wheels; HDBSCAN noise is never reassigned.

A separate seeded 2D UMAP creates visualization coordinates. The internal UMAP dimension may be higher. Cluster profiles contain common tags (at least 50% membership), mean shift distribution, dominant shift, strongest internal Phase 3 pairings, average membership, and low-confidence count.

Training produces a review result, not a registry model. Saving requires a non-empty name.

## Artifact and registry lifecycle

On training, a joblib bundle is written to `ML_ARTIFACT_DIR/staging`. Saving creates:

```text
ML_ARTIFACT_DIR/{model_id}/v{version}/model.joblib
ML_ARTIFACT_DIR/{model_id}/v{version}/manifest.json
```

The bundle contains preprocessing metadata, embeddings, feature vectors, both UMAP models, HDBSCAN, assignments, and profiles. `ml_model_artifact` stores only relative URI, checksum, and size.

Versions never overwrite. Activate demotes the previous active depot model to `SAVED`. Active models cannot be deleted. Duplicate returns configuration only. Archived packages remain reproducible.

## Model comparison

HDBSCAN labels are arbitrary. Comparison builds SPBU membership sets, calculates every cross-model Jaccard similarity, and applies Hungarian optimal matching. It reports stable neighborhoods, changed matched clusters, new/returning noise, new/removed SPBUs, splits, and merges.

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

- Jobs are synchronous because the repository has no worker/queue architecture. Persisted states and friendly errors make later worker migration straightforward, but very large depots should run behind an API timeout appropriate for ML workloads.
- The existing repository has no login provider. Header-based permission hooks are an integration seam, not production authentication.
- Node2Vec embeddings describe the training-period graph. Isolated/no-edge fallback deliberately contributes no pairing signal.
- The active-model interface serves saved assignments/profiles to later phases; Phase 6/7 logic is not implemented here.
