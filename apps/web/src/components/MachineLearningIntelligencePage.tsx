import ReactECharts from "echarts-for-react";
import { Archive, BrainCircuit, CheckCircle2, Copy, Eye, Play, RefreshCw, Save, Scale, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiGet, apiSend } from "../lib/api";

type Depot = { depot_id: string; depot_name: string };
type Tab = "concentration" | "clustering" | "registry";
type Readiness = {
  depot_id: string;
  depot_name: string;
  master_compatibility_pass_percentage: number;
  active_mt_count: number;
  active_spbu_count: number;
  evaluated_pair_count: number;
  passed_pair_count: number;
  failed_pair_count: number;
  is_ready: boolean;
  status: string;
  requirement: string;
  failure_counts: Record<string, number>;
  issue_examples: Array<{ mt_id: string; vehicle_registration: string | null; spbu_id: string; spbu_code: string; failed_rules: string[] }>;
};
type MTDistribution = {
  mt_id: string;
  mt_registration: string;
  shipment_count: number;
  historical_share: number;
  historically_used: boolean;
};
type ConcentrationProfile = {
  spbu_id: string;
  spbu_code: string;
  spbu_name: string | null;
  shipment_observation_count: number;
  compatible_mt_count: number;
  historically_used_mt_count: number;
  utilization_breadth: number;
  dominant_mt_id: string | null;
  dominant_mt_registration: string | null;
  dominant_mt_share: number;
  hhi: number;
  entropy: number;
  normalized_entropy: number;
  raw_ml_anomaly_score: number | null;
  concentration_anomaly_score: number | null;
  concentration_classification: string;
  data_sufficiency_status: string;
  peer_statistics: Record<string, string | number>;
  mt_distribution: MTDistribution[];
};
type ConcentrationRun = {
  analysis_run_id: string;
  depot_id: string;
  depot_name: string;
  baseline_start_date: string;
  baseline_end_date: string;
  minimum_shipment_observation: number;
  algorithm_version: string;
  status: string;
  created_by: string;
  created_at: string | null;
  summary: Record<string, number>;
  profiles: ConcentrationProfile[];
};
type RunSummary = Omit<ConcentrationRun, "summary" | "profiles" | "algorithm_version">;
type ShiftDefinition = { shift_id: string; name: string; start_time: string; end_time: string };
type DatasetSummary = {
  depot_name: string;
  training_start_date: string;
  training_end_date: string;
  shipment_count: number;
  source_shipment_count: number;
  spbu_count: number;
  mt_count: number;
  master_compatibility_pass_percentage: number;
  sufficient_history_spbu_count: number;
  excluded_insufficient_data_spbu_count: number;
  pairing_edge_count: number;
  isolated_spbu_count: number;
};
type Assignment = {
  spbu_id: string;
  spbu_code: string;
  spbu_name: string | null;
  shipment_observation_count?: number;
  cluster_id: number | null;
  cluster_label: string;
  membership_probability: number;
  is_noise: boolean;
  dominant_shift: string;
  key_tags: string[];
  visualization_x: number;
  visualization_y: number;
};
type ClusterProfile = {
  cluster_id: number;
  cluster_label: string;
  cluster_size: number;
  training_spbu_percentage: number;
  common_tags: Array<{ tag: string; member_count: number; member_share: number }>;
  shift_distribution: Array<{ shift_id: string; shift_name: string; share: number }>;
  dominant_shift: string;
  top_internal_pairings: Array<{ spbu_a_code: string; spbu_b_code: string; pair_count: number; pairing_strength: number }>;
  average_membership_probability: number;
  low_confidence_member_count: number;
};
type TrainingResult = {
  summary: {
    training_spbu_count: number;
    cluster_count: number;
    clustered_spbu_count: number;
    noise_spbu_count: number;
    average_membership_probability: number;
  };
  assignments: Assignment[];
  cluster_profiles: ClusterProfile[];
  warnings: string[];
  saved: boolean;
};
type TrainingRun = {
  training_run_id: string;
  depot_id: string;
  depot_name: string;
  training_start_date: string;
  training_end_date: string;
  minimum_shipment_observation: number;
  status: string;
  dataset_summary: DatasetSummary;
  shift_definition_snapshot: ShiftDefinition[];
  result: TrainingResult | Record<string, never>;
  error_message: string | null;
};
type ModelSummary = {
  model_id: string;
  model_name: string;
  model_description: string | null;
  model_version: number;
  depot_id: string;
  depot_name: string;
  training_start_date: string;
  training_end_date: string;
  training_spbu_count: number;
  cluster_count: number;
  noise_spbu_count: number;
  average_membership_probability: number;
  model_status: string;
  created_by: string;
  created_at: string | null;
};
type ModelDetail = ModelSummary & {
  feature_weights: Record<string, number>;
  node2vec_parameters: Record<string, string | number>;
  umap_parameters: Record<string, string | number>;
  hdbscan_parameters: Record<string, string | number>;
  shift_definition_snapshot: ShiftDefinition[];
  assignments: Assignment[];
  cluster_profiles: ClusterProfile[];
  library_versions: Record<string, string>;
};
type Comparison = {
  model_a: ModelSummary & { feature_weights: Record<string, number> };
  model_b: ModelSummary & { feature_weights: Record<string, number> };
  cluster_matches: Array<{ model_a_cluster_id: number; model_b_cluster_id: number; jaccard_similarity: number; intersection_count: number }>;
  stable_cluster_neighborhood_spbu_ids: string[];
  matched_cluster_changed_spbu_ids: string[];
  new_noise_spbu_ids: string[];
  noise_returning_to_cluster_spbu_ids: string[];
  cluster_splits: unknown[];
  cluster_merges: unknown[];
  methodology: string;
};

const defaultShifts: ShiftDefinition[] = [
  { shift_id: "shift_1", name: "Shift 1", start_time: "00:00", end_time: "05:59" },
  { shift_id: "shift_2", name: "Shift 2", start_time: "06:00", end_time: "11:59" },
  { shift_id: "shift_3", name: "Shift 3", start_time: "12:00", end_time: "17:59" },
  { shift_id: "shift_4", name: "Shift 4", start_time: "18:00", end_time: "23:59" }
];
const defaultConfig = {
  feature_weights: { tag: 0.4, shift: 0.25, pairing: 0.35 },
  node2vec_parameters: { dimensions: 16, walk_length: 20, num_walks: 40, p: 1, q: 1, window: 8, seed: 42 },
  umap_parameters: { n_neighbors: 15, n_components: 5, min_dist: 0.05, metric: "euclidean", random_state: 42 },
  hdbscan_parameters: { min_cluster_size: 5, min_samples: 3, metric: "euclidean", cluster_selection_method: "eom" },
  random_seed: 42
};

function pct(value: number | null | undefined, digits = 1) {
  return value === null || value === undefined ? "-" : `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
}

function score(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function label(value: string) {
  return value.replace(/_/g, " ");
}

function badgeClass(value: string) {
  if (["READY_FOR_MACHINE_LEARNING", "ACTIVE", "NORMAL", "COMPLETED", "DATASET_READY"].includes(value)) return "border-mint bg-mint/10 text-mint";
  if (["MODERATE_CONCENTRATION", "SAVED", "ARCHIVED"].includes(value)) return "border-amber bg-amber/10 text-amber";
  if (["INSUFFICIENT_DATA", "NOISE", "Noise / Unique Behavioral Pattern"].includes(value)) return "border-slate-300 bg-slate-50 text-slate-600";
  return "border-rust bg-rust/10 text-rust";
}

function Metric({ title, value, hint }: { title: string; value: string | number; hint?: string }) {
  return (
    <div className="border border-line bg-white p-4" title={hint}>
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</div>
      <div className="mt-2 text-2xl font-semibold text-petroink">{value}</div>
    </div>
  );
}

export function MachineLearningIntelligencePage({ depots }: { depots: Depot[] }) {
  const [tab, setTab] = useState<Tab>("concentration");
  const [depotId, setDepotId] = useState("");
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dateRange, setDateRange] = useState<{ min_date: string | null; max_date: string | null }>({ min_date: null, max_date: null });

  const [baselineStart, setBaselineStart] = useState("");
  const [baselineEnd, setBaselineEnd] = useState("");
  const [engineAMinimum, setEngineAMinimum] = useState("10");
  const [engineAAdvanced, setEngineAAdvanced] = useState(false);
  const [engineAEstimators, setEngineAEstimators] = useState("200");
  const [engineAContamination, setEngineAContamination] = useState("auto");
  const [engineASeed, setEngineASeed] = useState("42");
  const [engineAThresholds, setEngineAThresholds] = useState({ moderate: "40", high: "60", investigation: "80" });
  const [engineALoading, setEngineALoading] = useState(false);
  const [concentrationRun, setConcentrationRun] = useState<ConcentrationRun | null>(null);
  const [engineARuns, setEngineARuns] = useState<RunSummary[]>([]);
  const [selectedSavedRun, setSelectedSavedRun] = useState("");
  const [classificationFilter, setClassificationFilter] = useState("ALL");
  const [minimumScore, setMinimumScore] = useState("0");
  const [minimumObservationFilter, setMinimumObservationFilter] = useState("0");
  const [spbuSearch, setSpbuSearch] = useState("");
  const [scoreDirection, setScoreDirection] = useState<"desc" | "asc">("desc");
  const [selectedConcentration, setSelectedConcentration] = useState<ConcentrationProfile | null>(null);

  const [trainingStart, setTrainingStart] = useState("");
  const [trainingEnd, setTrainingEnd] = useState("");
  const [trainingMinimum, setTrainingMinimum] = useState("10");
  const [shiftDefinitions, setShiftDefinitions] = useState<ShiftDefinition[]>(defaultShifts);
  const [trainingConfig, setTrainingConfig] = useState(defaultConfig);
  const [engineBAdvanced, setEngineBAdvanced] = useState(false);
  const [engineBLoading, setEngineBLoading] = useState(false);
  const [trainingRun, setTrainingRun] = useState<TrainingRun | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<ClusterProfile | null>(null);
  const [saveDialog, setSaveDialog] = useState(false);
  const [modelName, setModelName] = useState("");
  const [modelDescription, setModelDescription] = useState("");

  const [models, setModels] = useState<ModelSummary[]>([]);
  const [registryLoading, setRegistryLoading] = useState(false);
  const [openedModel, setOpenedModel] = useState<ModelDetail | null>(null);
  const [compareA, setCompareA] = useState("");
  const [compareB, setCompareB] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);

  useEffect(() => {
    if (!depotId && depots.length) setDepotId(depots[0].depot_id);
  }, [depotId, depots]);

  useEffect(() => {
    if (!depotId) return;
    setReadinessLoading(true);
    setReadiness(null);
    setConcentrationRun(null);
    setTrainingRun(null);
    setError(null);
    Promise.all([
      apiGet<Readiness>(`/api/v1/phase5/readiness?depot_id=${encodeURIComponent(depotId)}`),
      apiGet<{ min_date: string | null; max_date: string | null }>(`/api/v1/affinity-intelligence/available-dates?depot_id=${encodeURIComponent(depotId)}`),
      apiGet<RunSummary[]>(`/api/v1/phase5/engine-a/runs?depot_id=${encodeURIComponent(depotId)}`)
    ])
      .then(([gate, dates, runs]) => {
        setReadiness(gate);
        setDateRange(dates);
        setEngineARuns(runs);
        if (dates.min_date && dates.max_date) {
          setBaselineStart(dates.min_date);
          setBaselineEnd(dates.max_date);
          setTrainingStart(dates.min_date);
          setTrainingEnd(dates.max_date);
        }
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Failed to load Phase 5 readiness."))
      .finally(() => setReadinessLoading(false));
  }, [depotId]);

  useEffect(() => {
    if (tab === "registry") void refreshRegistry();
  }, [tab, depotId]);

  async function refreshReadiness() {
    if (!depotId) return;
    setReadinessLoading(true);
    try {
      setReadiness(await apiGet<Readiness>(`/api/v1/phase5/readiness?depot_id=${encodeURIComponent(depotId)}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to refresh readiness.");
    } finally {
      setReadinessLoading(false);
    }
  }

  async function runEngineA() {
    if (!readiness?.is_ready || !baselineStart || !baselineEnd) return;
    setEngineALoading(true);
    setError(null);
    try {
      const payload = await apiSend<ConcentrationRun>("/api/v1/phase5/engine-a/analyze", "POST", {
        depot_id: depotId,
        baseline_start_date: baselineStart,
        baseline_end_date: baselineEnd,
        minimum_shipment_observation: Number(engineAMinimum),
        parameters: {
          n_estimators: Number(engineAEstimators),
          contamination: engineAContamination === "auto" ? "auto" : Number(engineAContamination),
          random_seed: Number(engineASeed),
          classification_thresholds: {
            moderate: Number(engineAThresholds.moderate),
            high: Number(engineAThresholds.high),
            investigation: Number(engineAThresholds.investigation)
          }
        }
      });
      setConcentrationRun(payload);
      setSelectedSavedRun(payload.analysis_run_id);
      setEngineARuns(await apiGet<RunSummary[]>(`/api/v1/phase5/engine-a/runs?depot_id=${encodeURIComponent(depotId)}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Concentration analysis failed.");
    } finally {
      setEngineALoading(false);
    }
  }

  async function openSavedRun() {
    if (!selectedSavedRun) return;
    setEngineALoading(true);
    try {
      setConcentrationRun(await apiGet<ConcentrationRun>(`/api/v1/phase5/engine-a/runs/${selectedSavedRun}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to open analysis run.");
    } finally {
      setEngineALoading(false);
    }
  }

  const filteredConcentration = useMemo(() => {
    if (!concentrationRun) return [];
    const needle = spbuSearch.trim().toLowerCase();
    return [...concentrationRun.profiles]
      .filter((row) => classificationFilter === "ALL" || row.concentration_classification === classificationFilter)
      .filter((row) => row.concentration_anomaly_score === null ? Number(minimumScore || 0) <= 0 : row.concentration_anomaly_score >= Number(minimumScore || 0))
      .filter((row) => row.shipment_observation_count >= Number(minimumObservationFilter || 0))
      .filter((row) => !needle || `${row.spbu_code} ${row.spbu_name ?? ""}`.toLowerCase().includes(needle))
      .sort((left, right) => {
        const difference = (left.concentration_anomaly_score ?? -1) - (right.concentration_anomaly_score ?? -1);
        return scoreDirection === "desc" ? -difference : difference;
      });
  }, [classificationFilter, concentrationRun, minimumObservationFilter, minimumScore, scoreDirection, spbuSearch]);

  const concentrationChartRows = useMemo(
    () => (concentrationRun?.profiles ?? []).filter((row) => row.concentration_anomaly_score !== null),
    [concentrationRun]
  );

  function updateWeight(key: "tag" | "shift" | "pairing", value: string) {
    setTrainingConfig((current) => ({ ...current, feature_weights: { ...current.feature_weights, [key]: Number(value) } }));
  }

  const weightTotal = Object.values(trainingConfig.feature_weights).reduce((total, value) => total + value, 0);

  async function prepareDataset() {
    if (!readiness?.is_ready || !trainingStart || !trainingEnd) return;
    setEngineBLoading(true);
    setError(null);
    setTrainingRun(null);
    try {
      setTrainingRun(await apiSend<TrainingRun>("/api/v1/phase5/engine-b/prepare-dataset", "POST", {
        depot_id: depotId,
        training_start_date: trainingStart,
        training_end_date: trainingEnd,
        minimum_shipment_observation: Number(trainingMinimum),
        shift_definitions: shiftDefinitions
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Dataset preparation failed.");
    } finally {
      setEngineBLoading(false);
    }
  }

  async function trainModel() {
    if (!trainingRun || !Number.isFinite(weightTotal) || Math.abs(weightTotal - 1) > 0.000001) return;
    setEngineBLoading(true);
    setError(null);
    try {
      setTrainingRun(await apiSend<TrainingRun>(`/api/v1/phase5/engine-b/training-runs/${trainingRun.training_run_id}/train`, "POST", { configuration: trainingConfig }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Model training failed.");
    } finally {
      setEngineBLoading(false);
    }
  }

  async function saveModel() {
    if (!trainingRun || !modelName.trim()) return;
    setEngineBLoading(true);
    try {
      await apiSend<ModelDetail>(`/api/v1/phase5/engine-b/training-runs/${trainingRun.training_run_id}/save`, "POST", {
        model_name: modelName.trim(),
        description: modelDescription.trim() || null
      });
      setSaveDialog(false);
      setModelName("");
      setModelDescription("");
      await refreshRegistry();
      setTab("registry");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Model save failed.");
    } finally {
      setEngineBLoading(false);
    }
  }

  async function refreshRegistry() {
    setRegistryLoading(true);
    try {
      setModels(await apiGet<ModelSummary[]>(`/api/v1/phase5/models${depotId ? `?depot_id=${encodeURIComponent(depotId)}` : ""}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to load Model Registry.");
    } finally {
      setRegistryLoading(false);
    }
  }

  async function openModel(modelId: string) {
    try {
      setOpenedModel(await apiGet<ModelDetail>(`/api/v1/phase5/models/${modelId}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to open model.");
    }
  }

  async function activateModel(modelId: string) {
    try {
      await apiSend(`/api/v1/phase5/models/${modelId}/activate`, "POST");
      await refreshRegistry();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Activation failed.");
    }
  }

  async function archiveModel(modelId: string) {
    try {
      await apiSend(`/api/v1/phase5/models/${modelId}/status`, "POST", { status: "ARCHIVED" });
      await refreshRegistry();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Archive failed.");
    }
  }

  async function duplicateModel(modelId: string) {
    try {
      const draft = await apiSend<{
        depot_id: string;
        training_start_date: string;
        training_end_date: string;
        minimum_shipment_observation: number;
        shift_definitions: ShiftDefinition[];
        feature_weights: typeof defaultConfig.feature_weights;
        node2vec_parameters: typeof defaultConfig.node2vec_parameters;
        umap_parameters: typeof defaultConfig.umap_parameters;
        hdbscan_parameters: typeof defaultConfig.hdbscan_parameters;
        random_seed: number;
      }>(`/api/v1/phase5/models/${modelId}/duplicate`, "POST");
      setDepotId(draft.depot_id);
      setTrainingStart(draft.training_start_date);
      setTrainingEnd(draft.training_end_date);
      setTrainingMinimum(String(draft.minimum_shipment_observation));
      setShiftDefinitions(draft.shift_definitions.map((shift, index) => ({
        shift_id: shift.shift_id || `shift_${index + 1}`,
        name: shift.name,
        start_time: shift.start_time,
        end_time: shift.end_time
      })));
      setTrainingConfig({
        feature_weights: draft.feature_weights,
        node2vec_parameters: draft.node2vec_parameters,
        umap_parameters: draft.umap_parameters,
        hdbscan_parameters: draft.hdbscan_parameters,
        random_seed: draft.random_seed
      });
      setTrainingRun(null);
      setTab("clustering");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Duplicate configuration failed.");
    }
  }

  async function deleteModel(model: ModelSummary) {
    if (!window.confirm(`Delete ${model.model_name} v${model.model_version}? Saved artifacts will also be removed.`)) return;
    try {
      await apiSend(`/api/v1/phase5/models/${model.model_id}`, "DELETE");
      await refreshRegistry();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Delete failed.");
    }
  }

  async function compareModels() {
    if (!compareA || !compareB || compareA === compareB) return;
    try {
      setComparison(await apiSend<Comparison>("/api/v1/phase5/models/compare", "POST", { model_a_id: compareA, model_b_id: compareB }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Model comparison failed.");
    }
  }

  const trainedResult = trainingRun?.result && "summary" in trainingRun.result ? trainingRun.result as TrainingResult : null;

  return (
    <div className="space-y-5">
      {error && <div className="flex items-start justify-between border border-rust bg-rust/5 px-4 py-3 text-sm text-rust"><span>{error}</span><button onClick={() => setError(null)}><X size={16} /></button></div>}

      <section className="border border-line bg-white p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-600"><BrainCircuit size={18} /> Phase 5 Readiness</div>
            <p className="mt-1 text-sm text-slate-500">Machine learning remains locked until every active MT–SPBU master pair in the selected depot passes the canonical compatibility rules.</p>
          </div>
          <div className="flex gap-2">
            <select className="min-w-64 border border-line bg-white px-3 py-2 text-sm" value={depotId} onChange={(event) => setDepotId(event.target.value)} title="Phase 5 depot">
              <option value="">Select Depot</option>
              {depots.map((depot) => <option key={depot.depot_id} value={depot.depot_id}>{depot.depot_name}</option>)}
            </select>
            <button className="border border-line px-3 py-2" onClick={refreshReadiness} disabled={!depotId || readinessLoading} title="Refresh compatibility readiness"><RefreshCw size={17} className={readinessLoading ? "animate-spin" : ""} /></button>
          </div>
        </div>
        {readiness && (
          <div className={`mt-4 grid gap-4 border p-4 lg:grid-cols-[1fr_1fr_auto] ${readiness.is_ready ? "border-mint bg-mint/5" : "border-rust bg-rust/5"}`}>
            <div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Master Compatibility</div><div className="mt-1 text-3xl font-semibold">{readiness.master_compatibility_pass_percentage.toFixed(2)}%</div><div className="mt-1 text-xs text-slate-500">{readiness.passed_pair_count.toLocaleString()} of {readiness.evaluated_pair_count.toLocaleString()} active master pairs pass</div></div>
            <div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Status</div><span className={`mt-2 inline-flex border px-3 py-1 text-sm font-semibold ${badgeClass(readiness.status)}`}>{label(readiness.status)}</span><p className="mt-2 text-xs text-slate-500">{readiness.requirement}</p></div>
            {!readiness.is_ready && <button className="self-center border border-rust px-3 py-2 text-sm font-semibold text-rust" onClick={() => { window.location.href = `/tag-consistency?depot_id=${encodeURIComponent(depotId)}`; }}>View Compatibility Issues</button>}
          </div>
        )}
      </section>

      <div className="flex flex-wrap gap-2 border-b border-line pb-3">
        {([
          ["concentration", "1. Historical MT–SPBU Anomaly"],
          ["clustering", "2. SPBU Behavioral Clustering"],
          ["registry", "3. Model Registry"]
        ] as Array<[Tab, string]>).map(([value, text]) => (
          <button key={value} className={`px-4 py-2 text-sm font-semibold ${tab === value ? "bg-petroblue text-white" : "border border-line bg-white text-slate-600"}`} onClick={() => setTab(value)}>{text}</button>
        ))}
      </div>

      {tab === "concentration" && (
        <>
          <section className="border border-line bg-white p-5">
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-display text-xl font-semibold">Historical MT–SPBU Concentration Anomaly</h2><p className="mt-1 text-sm text-slate-500">Find unexpected historical concentration relative to compatible fleet opportunity. This is not an assignment-error classifier.</p></div><div className="flex gap-2"><select className="border border-line bg-white px-3 py-2 text-sm" value={selectedSavedRun} onChange={(event) => setSelectedSavedRun(event.target.value)}><option value="">Saved analysis runs</option>{engineARuns.map((run) => <option key={run.analysis_run_id} value={run.analysis_run_id}>{run.baseline_start_date}–{run.baseline_end_date} · {run.status}</option>)}</select><button className="border border-line px-3 py-2 text-sm" onClick={openSavedRun} disabled={!selectedSavedRun}>Open</button></div></div>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-5">
              <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Depot<input className="mt-1 w-full border border-line bg-slate-50 px-3 py-2 text-sm" value={readiness?.depot_name ?? ""} readOnly /></label>
              <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Baseline Start Date<input className="mt-1 w-full border border-line px-3 py-2 text-sm" type="date" min={dateRange.min_date ?? undefined} max={dateRange.max_date ?? undefined} value={baselineStart} onChange={(event) => setBaselineStart(event.target.value)} /></label>
              <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Baseline End Date<input className="mt-1 w-full border border-line px-3 py-2 text-sm" type="date" min={dateRange.min_date ?? undefined} max={dateRange.max_date ?? undefined} value={baselineEnd} onChange={(event) => setBaselineEnd(event.target.value)} /></label>
              <label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Minimum Shipment Observation<input className="mt-1 w-full border border-line px-3 py-2 text-sm" type="number" min="1" value={engineAMinimum} onChange={(event) => setEngineAMinimum(event.target.value)} /></label>
              <button className="mt-5 inline-flex items-center justify-center gap-2 bg-mint px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40" disabled={!readiness?.is_ready || engineALoading || !baselineStart || !baselineEnd} onClick={runEngineA}><Play size={16} /> {engineALoading ? "Running…" : "Run Analysis"}</button>
            </div>
            <button className="mt-4 text-sm font-semibold text-petroblue" onClick={() => setEngineAAdvanced((value) => !value)}>Advanced Settings {engineAAdvanced ? "▴" : "▾"}</button>
            {engineAAdvanced && <div className="mt-3 grid gap-3 border border-line bg-slate-50 p-4 md:grid-cols-3 lg:grid-cols-6"><label className="text-xs">Estimators<input className="mt-1 w-full border border-line px-2 py-2" type="number" value={engineAEstimators} onChange={(event) => setEngineAEstimators(event.target.value)} /></label><label className="text-xs">Contamination<input className="mt-1 w-full border border-line px-2 py-2" value={engineAContamination} onChange={(event) => setEngineAContamination(event.target.value)} /></label><label className="text-xs">Random Seed<input className="mt-1 w-full border border-line px-2 py-2" type="number" value={engineASeed} onChange={(event) => setEngineASeed(event.target.value)} /></label>{(["moderate", "high", "investigation"] as const).map((key) => <label className="text-xs" key={key}>{key} threshold<input className="mt-1 w-full border border-line px-2 py-2" type="number" value={engineAThresholds[key]} onChange={(event) => setEngineAThresholds((current) => ({ ...current, [key]: event.target.value }))} /></label>)}<button className="text-left text-xs font-semibold text-petroblue" onClick={() => { setEngineAEstimators("200"); setEngineAContamination("auto"); setEngineASeed("42"); setEngineAThresholds({ moderate: "40", high: "60", investigation: "80" }); }}>Reset defaults</button></div>}
          </section>

          {concentrationRun && (
            <>
              <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Metric title="Analyzed SPBU" value={concentrationRun.summary.spbu_count ?? 0} /><Metric title="Sufficient Evidence" value={concentrationRun.summary.sufficient_data_count ?? 0} /><Metric title="Insufficient Data" value={concentrationRun.summary.insufficient_data_count ?? 0} /><Metric title="Investigation Recommended" value={concentrationRun.summary.investigation_recommended_count ?? 0} /></section>
              <section className="grid gap-4 lg:grid-cols-3">
                <div className="border border-line bg-white p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Anomaly Ranking</h3><ReactECharts style={{ height: 300 }} option={{ grid: { left: 55, right: 20, bottom: 70 }, xAxis: { type: "category", data: concentrationChartRows.slice(0, 15).map((row) => row.spbu_code), axisLabel: { rotate: 45 } }, yAxis: { type: "value", min: 0, max: 100 }, tooltip: { trigger: "axis" }, series: [{ type: "bar", data: concentrationChartRows.slice(0, 15).map((row) => row.concentration_anomaly_score) }] }} /></div>
                <div className="border border-line bg-white p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Compatibility vs Actual Usage</h3><ReactECharts style={{ height: 300 }} option={{ xAxis: { name: "Compatible MT" }, yAxis: { name: "Historically Used MT" }, tooltip: { formatter: (params: { data: { name: string; value: number[] } }) => `${params.data.name}<br/>Compatible: ${params.data.value[0]}<br/>Used: ${params.data.value[1]}<br/>Score: ${params.data.value[2]}` }, series: [{ type: "scatter", symbolSize: 10, data: concentrationChartRows.map((row) => ({ name: row.spbu_code, value: [row.compatible_mt_count, row.historically_used_mt_count, row.concentration_anomaly_score] })) }] }} /></div>
                <div className="border border-line bg-white p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Dominant Share vs Utilization Breadth</h3><ReactECharts style={{ height: 300 }} option={{ xAxis: { name: "Utilization Breadth", min: 0, max: 1 }, yAxis: { name: "Dominant MT Share", min: 0, max: 1 }, tooltip: { formatter: (params: { data: { name: string; value: number[] } }) => `${params.data.name}<br/>Breadth: ${pct(params.data.value[0])}<br/>Dominant: ${pct(params.data.value[1])}<br/>Score: ${params.data.value[2]}` }, series: [{ type: "scatter", symbolSize: 10, data: concentrationChartRows.map((row) => ({ name: row.spbu_code, value: [row.utilization_breadth, row.dominant_mt_share, row.concentration_anomaly_score] })) }] }} /></div>
              </section>
              <section className="border border-line bg-white p-4">
                <div className="mb-4 flex flex-wrap gap-3"><select className="border border-line px-3 py-2 text-sm" value={classificationFilter} onChange={(event) => setClassificationFilter(event.target.value)}><option value="ALL">All classifications</option>{["NORMAL", "MODERATE_CONCENTRATION", "HIGH_CONCENTRATION", "INVESTIGATION_RECOMMENDED", "INSUFFICIENT_DATA"].map((value) => <option key={value} value={value}>{label(value)}</option>)}</select><input className="border border-line px-3 py-2 text-sm" type="number" min="0" max="100" value={minimumScore} onChange={(event) => setMinimumScore(event.target.value)} placeholder="Minimum anomaly score" title="Minimum anomaly score" /><input className="border border-line px-3 py-2 text-sm" type="number" min="0" value={minimumObservationFilter} onChange={(event) => setMinimumObservationFilter(event.target.value)} placeholder="Minimum observation" title="Minimum observation" /><input className="min-w-60 border border-line px-3 py-2 text-sm" value={spbuSearch} onChange={(event) => setSpbuSearch(event.target.value)} placeholder="Search SPBU" /></div>
                <div className="overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr className="border-b border-line bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500"><th className="px-3 py-2">SPBU</th><th className="px-3 py-2">Observations</th><th className="px-3 py-2">Compatible MT</th><th className="px-3 py-2">Used MT</th><th className="px-3 py-2" title="Historically used compatible MT divided by compatible MT.">Utilization Breadth</th><th className="px-3 py-2">Dominant MT</th><th className="px-3 py-2" title="Largest historical P(MT | SPBU).">Dominant Share</th><th className="px-3 py-2" title="Higher HHI means a smaller fleet dominates historical assignments.">HHI</th><th className="px-3 py-2" title="Shannon entropy normalized by the number of historically used MTs; higher means more even usage.">Normalized Entropy</th><th className="px-3 py-2" title="Negative Isolation Forest score_samples value; higher is more unusual within the run.">Raw ML</th><th className="px-3 py-2" title="Raw ML severity min-max scaled to 0–100 within this run; higher means more unusual concentration."><button onClick={() => setScoreDirection((value) => value === "desc" ? "asc" : "desc")}>Anomaly Score {scoreDirection === "desc" ? "↓" : "↑"}</button></th><th className="px-3 py-2">Classification</th></tr></thead><tbody>{filteredConcentration.map((row) => <tr key={row.spbu_id} className="cursor-pointer border-b border-line hover:bg-petrocloud/50" onClick={() => setSelectedConcentration(row)}><td className="px-3 py-2"><div className="font-semibold">{row.spbu_code}</div><div className="text-xs text-slate-500">{row.spbu_name}</div></td><td className="px-3 py-2">{row.shipment_observation_count}</td><td className="px-3 py-2">{row.compatible_mt_count}</td><td className="px-3 py-2">{row.historically_used_mt_count}</td><td className="px-3 py-2">{pct(row.utilization_breadth)}</td><td className="px-3 py-2">{row.dominant_mt_registration ?? "-"}</td><td className="px-3 py-2">{pct(row.dominant_mt_share)}</td><td className="px-3 py-2">{score(row.hhi)}</td><td className="px-3 py-2">{score(row.normalized_entropy)}</td><td className="px-3 py-2">{score(row.raw_ml_anomaly_score)}</td><td className="px-3 py-2 font-semibold">{score(row.concentration_anomaly_score)}</td><td className="px-3 py-2"><span className={`whitespace-nowrap border px-2 py-1 text-xs ${badgeClass(row.concentration_classification)}`}>{label(row.concentration_classification)}</span></td></tr>)}{filteredConcentration.length === 0 && <tr><td colSpan={12} className="px-3 py-8 text-center text-slate-500">No profiles match the active filters.</td></tr>}</tbody></table></div>
              </section>
            </>
          )}
        </>
      )}

      {tab === "clustering" && (
        <>
          <section className="border border-line bg-white p-5">
            <h2 className="font-display text-xl font-semibold">SPBU Behavioral Clustering</h2><p className="mt-1 text-sm text-slate-500">Tag + full shift distribution + Phase 3 co-shipment graph. Clusters describe behavior and never override compatibility.</p>
            <div className="mt-4 grid gap-3 md:grid-cols-2 lg:grid-cols-5"><label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Depot<input className="mt-1 w-full border border-line bg-slate-50 px-3 py-2 text-sm" value={readiness?.depot_name ?? ""} readOnly /></label><label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Training Start Date<input className="mt-1 w-full border border-line px-3 py-2 text-sm" type="date" value={trainingStart} onChange={(event) => setTrainingStart(event.target.value)} /></label><label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Training End Date<input className="mt-1 w-full border border-line px-3 py-2 text-sm" type="date" value={trainingEnd} onChange={(event) => setTrainingEnd(event.target.value)} /></label><label className="text-xs font-semibold uppercase tracking-wide text-slate-500">Minimum Shipment Observation<input className="mt-1 w-full border border-line px-3 py-2 text-sm" type="number" min="1" value={trainingMinimum} onChange={(event) => setTrainingMinimum(event.target.value)} /></label><button className="mt-5 bg-mint px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={!readiness?.is_ready || engineBLoading || !trainingStart || !trainingEnd} onClick={prepareDataset}>{engineBLoading && !trainingRun ? "Preparing…" : "Prepare Training Dataset"}</button></div>
            <div className="mt-4 border border-line bg-slate-50 p-4"><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Shift Definition Snapshot</div><div className="mt-3 grid gap-2 md:grid-cols-2 lg:grid-cols-4">{shiftDefinitions.map((shift, index) => <div className="border border-line bg-white p-3" key={shift.shift_id}><input className="w-full border-b border-line pb-1 text-sm font-semibold" value={shift.name} onChange={(event) => setShiftDefinitions((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, name: event.target.value } : row))} /><div className="mt-2 flex items-center gap-2"><input className="w-full border border-line p-1 text-xs" type="time" value={shift.start_time} onChange={(event) => setShiftDefinitions((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, start_time: event.target.value } : row))} /><span>–</span><input className="w-full border border-line p-1 text-xs" type="time" value={shift.end_time} onChange={(event) => setShiftDefinitions((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, end_time: event.target.value } : row))} /></div></div>)}</div></div>
          </section>

          {trainingRun && <section className="border border-line bg-white p-5"><div className="flex items-center justify-between"><div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Training Dataset</div><h3 className="mt-1 text-lg font-semibold">{trainingRun.status === "DATASET_READY" ? "Dataset Ready for Validation" : label(trainingRun.status)}</h3></div><span className={`border px-3 py-1 text-xs font-semibold ${badgeClass(trainingRun.status)}`}>{label(trainingRun.status)}</span></div><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5"><Metric title="Shipments" value={trainingRun.dataset_summary.shipment_count ?? 0} /><Metric title="SPBU in Period" value={trainingRun.dataset_summary.spbu_count ?? 0} /><Metric title="Sufficient History" value={trainingRun.dataset_summary.sufficient_history_spbu_count ?? 0} /><Metric title="Excluded" value={trainingRun.dataset_summary.excluded_insufficient_data_spbu_count ?? 0} /><Metric title="Pairing Edges" value={trainingRun.dataset_summary.pairing_edge_count ?? 0} /></div>
            <button className="mt-4 text-sm font-semibold text-petroblue" onClick={() => setEngineBAdvanced((value) => !value)}>Advanced Model Settings {engineBAdvanced ? "▴" : "▾"}</button>
            {engineBAdvanced && <div className="mt-3 space-y-4 border border-line bg-slate-50 p-4"><div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Feature Weights · total {(weightTotal * 100).toFixed(0)}%</div><div className="mt-2 grid gap-3 md:grid-cols-3">{(["tag", "shift", "pairing"] as const).map((key) => <label className="text-sm capitalize" key={key}>{key}<input className="mt-1 w-full border border-line px-3 py-2" type="number" min="0" max="1" step="0.05" value={trainingConfig.feature_weights[key]} onChange={(event) => updateWeight(key, event.target.value)} /></label>)}</div>{Math.abs(weightTotal - 1) > 0.000001 && <div className="mt-2 text-xs font-semibold text-rust">Weights must equal exactly 1.00.</div>}</div><div className="grid gap-4 lg:grid-cols-3">{(["node2vec_parameters", "umap_parameters", "hdbscan_parameters"] as const).map((group) => <div key={group}><div className="text-xs font-semibold uppercase tracking-wide text-slate-500" title={group === "node2vec_parameters" ? "Node2Vec converts the weighted SPBU co-shipment graph into numeric pairing vectors." : group === "umap_parameters" ? "UMAP reduces the fused feature space while preserving local behavioral neighborhoods." : "HDBSCAN discovers variable-density clusters and may leave unique SPBUs as noise."}>{group.replace("_parameters", "")}</div><div className="mt-2 grid grid-cols-2 gap-2">{Object.entries(trainingConfig[group]).map(([key, value]) => <label className="text-xs" key={key}>{key}<input className="mt-1 w-full border border-line p-2" value={value} onChange={(event) => setTrainingConfig((current) => ({ ...current, [group]: { ...current[group], [key]: typeof value === "number" ? Number(event.target.value) : event.target.value } }))} /></label>)}</div></div>)}</div><div className="grid gap-2 text-xs text-slate-500 lg:grid-cols-3"><p><strong>Node2Vec:</strong> turns Phase 3 pairing relationships into a vector while preserving graph neighborhoods.</p><p><strong>UMAP:</strong> reduces fused behavior features before clustering and separately creates the 2D map.</p><p><strong>HDBSCAN:</strong> finds natural density groups without selecting a cluster count and retains noise as a valid outcome.</p></div><button className="text-xs font-semibold text-petroblue" onClick={() => setTrainingConfig(defaultConfig)}>Reset defaults</button></div>}
            {!trainedResult && <div className="mt-4 flex gap-2"><button className="inline-flex items-center gap-2 bg-petroblue px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" onClick={trainModel} disabled={engineBLoading || Math.abs(weightTotal - 1) > 0.000001}><Play size={16} /> {engineBLoading ? "Training…" : "Train Model"}</button><button className="border border-line px-4 py-2 text-sm" onClick={() => setTrainingRun(null)}>Discard Dataset</button></div>}
          </section>}

          {trainedResult && <><section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5"><Metric title="Training SPBU" value={trainedResult.summary.training_spbu_count} /><Metric title="Clusters" value={trainedResult.summary.cluster_count} /><Metric title="Clustered SPBU" value={trainedResult.summary.clustered_spbu_count} /><Metric title="Noise / Unique Pattern" value={trainedResult.summary.noise_spbu_count} hint="Not an error; these SPBUs do not strongly match a discovered cluster." /><Metric title="Average Membership" value={pct(trainedResult.summary.average_membership_probability)} /></section>{trainedResult.warnings.map((warning) => <div key={warning} className="border border-amber bg-amber/5 px-4 py-3 text-sm text-amber">{warning}</div>)}<section className="grid gap-4 lg:grid-cols-2"><div className="border border-line bg-white p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">UMAP Cluster Map</h3><ReactECharts style={{ height: 420 }} option={{ tooltip: { formatter: (params: { data: { name: string; value: number[]; detail: Assignment } }) => `${params.data.name}<br/>${params.data.detail.cluster_label}<br/>Membership: ${pct(params.data.detail.membership_probability)}<br/>${params.data.detail.dominant_shift}<br/>${params.data.detail.key_tags.slice(0, 3).join(", ")}` }, xAxis: { show: false }, yAxis: { show: false }, series: Array.from(new Set(trainedResult.assignments.map((row) => row.cluster_label))).map((clusterLabel) => ({ name: clusterLabel, type: "scatter", symbolSize: 11, data: trainedResult.assignments.filter((row) => row.cluster_label === clusterLabel).map((row) => ({ name: row.spbu_code, value: [row.visualization_x, row.visualization_y], detail: row })) })) }} /></div><div className="border border-line bg-white p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Cluster Profiles</h3><div className="mt-3 max-h-[420px] space-y-3 overflow-y-auto">{trainedResult.cluster_profiles.map((profile) => <button className="w-full border border-line p-3 text-left hover:bg-petrocloud" key={profile.cluster_id} onClick={() => setSelectedCluster(profile)}><div className="flex items-center justify-between"><span className="font-semibold">{profile.cluster_label}</span><span className="text-sm">{profile.cluster_size} members</span></div><div className="mt-2 text-sm text-slate-600">Dominant shift: {profile.dominant_shift}</div><div className="mt-1 text-xs text-slate-500">{profile.common_tags.slice(0, 3).map((tag) => tag.tag).join(" · ") || "No tag shared by at least 50% of members"}</div><div className="mt-1 text-xs text-slate-500">Average membership {pct(profile.average_membership_probability)} · {profile.low_confidence_member_count} low-confidence</div></button>)}</div></div></section><section className="border border-line bg-white p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Cluster Membership</h3><div className="mt-3 overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b border-line bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500"><th className="px-3 py-2">SPBU</th><th className="px-3 py-2">Cluster</th><th className="px-3 py-2" title="HDBSCAN confidence that the SPBU belongs in its cluster.">Membership Probability</th><th className="px-3 py-2" title="Not an error; may represent a unique operational pattern.">Noise / Outlier</th><th className="px-3 py-2">Dominant Shift</th><th className="px-3 py-2">Key Tags</th></tr></thead><tbody>{trainedResult.assignments.map((row) => <tr className="border-b border-line" key={row.spbu_id}><td className="px-3 py-2"><div className="font-semibold">{row.spbu_code}</div><div className="text-xs text-slate-500">{row.spbu_name}</div></td><td className="px-3 py-2">{row.cluster_label}</td><td className="px-3 py-2">{pct(row.membership_probability)}</td><td className="px-3 py-2">{row.is_noise ? "Yes" : "No"}</td><td className="px-3 py-2">{row.dominant_shift}</td><td className="max-w-72 px-3 py-2 text-xs">{row.key_tags.slice(0, 4).join(", ") || "-"}</td></tr>)}</tbody></table></div><div className="mt-4 flex flex-wrap gap-2"><button className="inline-flex items-center gap-2 bg-mint px-4 py-2 text-sm font-semibold text-white" onClick={() => setSaveDialog(true)}><Save size={16} /> Save Model</button><button className="border border-line px-4 py-2 text-sm" onClick={() => setTrainingRun(null)}>Discard Result</button><button className="border border-line px-4 py-2 text-sm" onClick={() => setTrainingRun((current) => current ? { ...current, result: {} } : current)}>Adjust Parameters & Retrain</button></div></section></>}
        </>
      )}

      {tab === "registry" && <><section className="border border-line bg-white p-5"><div className="flex items-center justify-between"><div><h2 className="font-display text-xl font-semibold">Model Registry</h2><p className="mt-1 text-sm text-slate-500">Saved, versioned Engine B packages. Only one model may be active per depot.</p></div><button className="inline-flex items-center gap-2 border border-line px-3 py-2 text-sm" onClick={refreshRegistry}><RefreshCw size={16} className={registryLoading ? "animate-spin" : ""} /> Refresh</button></div><div className="mt-4 overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b border-line bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500"><th className="px-3 py-2">Model Name</th><th className="px-3 py-2">Version</th><th className="px-3 py-2">Depot</th><th className="px-3 py-2">Training Period</th><th className="px-3 py-2">SPBU</th><th className="px-3 py-2">Clusters</th><th className="px-3 py-2">Noise</th><th className="px-3 py-2">Created</th><th className="px-3 py-2">Created By</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Actions</th></tr></thead><tbody>{models.map((model) => <tr className="border-b border-line" key={model.model_id}><td className="px-3 py-2 font-semibold">{model.model_name}</td><td className="px-3 py-2">v{model.model_version}</td><td className="px-3 py-2">{model.depot_name}</td><td className="whitespace-nowrap px-3 py-2">{model.training_start_date} – {model.training_end_date}</td><td className="px-3 py-2">{model.training_spbu_count}</td><td className="px-3 py-2">{model.cluster_count}</td><td className="px-3 py-2">{model.noise_spbu_count}</td><td className="whitespace-nowrap px-3 py-2">{model.created_at ? new Date(model.created_at).toLocaleString() : "-"}</td><td className="px-3 py-2">{model.created_by}</td><td className="px-3 py-2"><span className={`border px-2 py-1 text-xs ${badgeClass(model.model_status)}`}>{model.model_status}</span></td><td className="px-3 py-2"><div className="flex gap-1"><button className="border border-line p-2" onClick={() => openModel(model.model_id)} title="Open"><Eye size={15} /></button>{model.model_status !== "ACTIVE" && <button className="border border-line p-2" onClick={() => activateModel(model.model_id)} title="Activate"><CheckCircle2 size={15} /></button>}<button className="border border-line p-2" onClick={() => duplicateModel(model.model_id)} title="Duplicate configuration"><Copy size={15} /></button>{model.model_status !== "ARCHIVED" && <button className="border border-line p-2" onClick={() => archiveModel(model.model_id)} title="Archive"><Archive size={15} /></button>}<button className="border border-line p-2 text-rust disabled:opacity-30" onClick={() => deleteModel(model)} disabled={model.model_status === "ACTIVE"} title="Delete"><Trash2 size={15} /></button></div></td></tr>)}{models.length === 0 && <tr><td colSpan={11} className="px-3 py-8 text-center text-slate-500">No saved models for this depot.</td></tr>}</tbody></table></div></section>
        <section className="border border-line bg-white p-5"><div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-600"><Scale size={17} /> Compare Models</div><div className="mt-3 flex flex-wrap gap-2"><select className="min-w-64 border border-line px-3 py-2 text-sm" value={compareA} onChange={(event) => setCompareA(event.target.value)}><option value="">Model A</option>{models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_name} v{model.model_version}</option>)}</select><select className="min-w-64 border border-line px-3 py-2 text-sm" value={compareB} onChange={(event) => setCompareB(event.target.value)}><option value="">Model B</option>{models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_name} v{model.model_version}</option>)}</select><button className="bg-petroblue px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={!compareA || !compareB || compareA === compareB} onClick={compareModels}>Compare</button></div>{comparison && <div className="mt-4 space-y-4"><p className="text-xs text-slate-500">{comparison.methodology}</p><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6"><Metric title="Matched Clusters" value={comparison.cluster_matches.length} /><Metric title="Stable Neighborhood" value={comparison.stable_cluster_neighborhood_spbu_ids.length} /><Metric title="Changed Neighborhood" value={comparison.matched_cluster_changed_spbu_ids.length} /><Metric title="New Noise" value={comparison.new_noise_spbu_ids.length} /><Metric title="Returned from Noise" value={comparison.noise_returning_to_cluster_spbu_ids.length} /><Metric title="Splits / Merges" value={`${comparison.cluster_splits.length} / ${comparison.cluster_merges.length}`} /></div><div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b border-line bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500"><th className="px-3 py-2">Model A Cluster</th><th className="px-3 py-2">Matched Model B Cluster</th><th className="px-3 py-2">Jaccard Similarity</th><th className="px-3 py-2">Shared SPBU</th></tr></thead><tbody>{comparison.cluster_matches.map((match) => <tr className="border-b border-line" key={`${match.model_a_cluster_id}-${match.model_b_cluster_id}`}><td className="px-3 py-2">Cluster {match.model_a_cluster_id + 1}</td><td className="px-3 py-2">Cluster {match.model_b_cluster_id + 1}</td><td className="px-3 py-2">{pct(match.jaccard_similarity)}</td><td className="px-3 py-2">{match.intersection_count}</td></tr>)}</tbody></table></div></div>}</section></>}

      {selectedConcentration && <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/35"><div className="h-full w-full max-w-3xl overflow-y-auto bg-white p-5 shadow-xl"><div className="flex items-start justify-between"><div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">SPBU Concentration Detail</div><h2 className="mt-1 text-xl font-semibold">{selectedConcentration.spbu_code} · {selectedConcentration.spbu_name}</h2><p className="mt-1 text-sm text-slate-500">Baseline {concentrationRun?.baseline_start_date} – {concentrationRun?.baseline_end_date}</p></div><button className="border border-line p-2" onClick={() => setSelectedConcentration(null)}><X size={17} /></button></div><div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Metric title="Compatible MT" value={selectedConcentration.compatible_mt_count} /><Metric title="Historically Used" value={selectedConcentration.historically_used_mt_count} /><Metric title="Dominant Share" value={pct(selectedConcentration.dominant_mt_share)} /><Metric title="Anomaly Score" value={score(selectedConcentration.concentration_anomaly_score)} /><Metric title="Utilization Breadth" value={pct(selectedConcentration.utilization_breadth)} /><Metric title="HHI" value={score(selectedConcentration.hhi)} /><Metric title="Entropy" value={score(selectedConcentration.entropy)} /><Metric title="Normalized Entropy" value={score(selectedConcentration.normalized_entropy)} /></div><div className="mt-5 border border-line p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Historical MT Distribution</h3><ReactECharts style={{ height: 320 }} option={{ grid: { left: 60, right: 20, bottom: 80 }, xAxis: { type: "category", data: selectedConcentration.mt_distribution.filter((row) => row.historically_used).map((row) => row.mt_registration), axisLabel: { rotate: 45 } }, yAxis: { type: "value", name: "Shipment count" }, tooltip: { trigger: "axis" }, series: [{ type: "bar", data: selectedConcentration.mt_distribution.filter((row) => row.historically_used).map((row) => row.shipment_count) }] }} /></div><div className="mt-5 border border-line p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Peer Context</h3><div className="mt-3 grid grid-cols-2 gap-3 text-sm">{Object.entries(selectedConcentration.peer_statistics).map(([key, value]) => <div key={key}><span className="text-slate-500">{label(key)}:</span> <span className="font-semibold">{String(value)}</span></div>)}</div></div><div className="mt-5 border border-line p-4"><h3 className="text-sm font-semibold uppercase tracking-wide text-slate-600">Compatible but Historically Unused MT</h3><div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{selectedConcentration.mt_distribution.filter((row) => !row.historically_used).map((row) => <div className="border border-line px-3 py-2 text-sm" key={row.mt_id}>{row.mt_registration}</div>)}{selectedConcentration.mt_distribution.every((row) => row.historically_used) && <div className="text-sm text-slate-500">Every compatible MT was historically used.</div>}</div></div></div></div>}

      {selectedCluster && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/35 p-4"><div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto bg-white p-5 shadow-xl"><div className="flex justify-between"><div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Cluster Detail</div><h2 className="mt-1 text-xl font-semibold">{selectedCluster.cluster_label}</h2></div><button className="border border-line p-2" onClick={() => setSelectedCluster(null)}><X size={17} /></button></div><div className="mt-4 grid gap-3 sm:grid-cols-3"><Metric title="Members" value={selectedCluster.cluster_size} /><Metric title="Average Membership" value={pct(selectedCluster.average_membership_probability)} /><Metric title="Low Confidence" value={selectedCluster.low_confidence_member_count} /></div><div className="mt-4 grid gap-4 lg:grid-cols-2"><div className="border border-line p-4"><h3 className="font-semibold">Tag Profile</h3><div className="mt-2 space-y-2 text-sm">{selectedCluster.common_tags.map((tag) => <div key={tag.tag}>{tag.tag} · {pct(tag.member_share)}</div>)}</div></div><div className="border border-line p-4"><h3 className="font-semibold">Shift Profile</h3><div className="mt-2 space-y-2 text-sm">{selectedCluster.shift_distribution.map((shift) => <div key={shift.shift_id}>{shift.shift_name} · {pct(shift.share)}</div>)}</div></div></div><div className="mt-4 border border-line p-4"><h3 className="font-semibold">Member SPBUs</h3><div className="mt-2 grid gap-2 sm:grid-cols-2">{trainedResult?.assignments.filter((assignment) => assignment.cluster_id === selectedCluster.cluster_id && !assignment.is_noise).map((assignment) => <div className="border border-line px-3 py-2 text-sm" key={assignment.spbu_id}><span className="font-semibold">{assignment.spbu_code}</span> · {assignment.spbu_name}</div>)}</div></div><div className="mt-4 border border-line p-4"><h3 className="font-semibold">Top Internal Pairings</h3><div className="mt-2 space-y-2 text-sm">{selectedCluster.top_internal_pairings.map((pair) => <div key={`${pair.spbu_a_code}-${pair.spbu_b_code}`}>{pair.spbu_a_code} ↔ {pair.spbu_b_code} · {pair.pair_count} shipments · strength {pct(pair.pairing_strength)}</div>)}{selectedCluster.top_internal_pairings.length === 0 && <div className="text-slate-500">No internal co-shipment edges.</div>}</div></div></div></div>}

      {saveDialog && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/35 p-4"><div className="w-full max-w-lg bg-white p-5 shadow-xl"><div className="flex justify-between"><h2 className="text-lg font-semibold">Save Behavioral Model</h2><button onClick={() => setSaveDialog(false)}><X size={17} /></button></div><label className="mt-4 block text-xs font-semibold uppercase tracking-wide text-slate-500">Model Name *<input className="mt-1 w-full border border-line px-3 py-2 text-sm" value={modelName} onChange={(event) => setModelName(event.target.value)} placeholder="Balongan Behavioral Cluster 2025" /></label><label className="mt-3 block text-xs font-semibold uppercase tracking-wide text-slate-500">Description<textarea className="mt-1 w-full border border-line px-3 py-2 text-sm" rows={4} value={modelDescription} onChange={(event) => setModelDescription(event.target.value)} /></label><div className="mt-4 flex justify-end gap-2"><button className="border border-line px-4 py-2 text-sm" onClick={() => setSaveDialog(false)}>Cancel</button><button className="bg-mint px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={!modelName.trim() || engineBLoading} onClick={saveModel}>{engineBLoading ? "Saving…" : "Save Model"}</button></div></div></div>}

      {openedModel && <div className="fixed inset-0 z-50 flex justify-end bg-slate-900/35"><div className="h-full w-full max-w-4xl overflow-y-auto bg-white p-5 shadow-xl"><div className="flex justify-between"><div><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Saved Model Package</div><h2 className="mt-1 text-xl font-semibold">{openedModel.model_name} v{openedModel.model_version}</h2><p className="mt-1 text-sm text-slate-500">{openedModel.model_description}</p></div><button className="border border-line p-2" onClick={() => setOpenedModel(null)}><X size={17} /></button></div><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Metric title="SPBU" value={openedModel.training_spbu_count} /><Metric title="Clusters" value={openedModel.cluster_count} /><Metric title="Noise" value={openedModel.noise_spbu_count} /><Metric title="Average Membership" value={pct(openedModel.average_membership_probability)} /></div><div className="mt-4 border border-line p-4"><h3 className="font-semibold">Reproducibility Package</h3><pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs text-slate-600">{JSON.stringify({ feature_weights: openedModel.feature_weights, node2vec: openedModel.node2vec_parameters, umap: openedModel.umap_parameters, hdbscan: openedModel.hdbscan_parameters, libraries: openedModel.library_versions }, null, 2)}</pre></div><div className="mt-4 overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b border-line bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500"><th className="px-3 py-2">SPBU</th><th className="px-3 py-2">Cluster</th><th className="px-3 py-2">Membership</th><th className="px-3 py-2">Dominant Shift</th></tr></thead><tbody>{openedModel.assignments.map((assignment) => <tr className="border-b border-line" key={assignment.spbu_id}><td className="px-3 py-2">{assignment.spbu_code}</td><td className="px-3 py-2">{assignment.cluster_label}</td><td className="px-3 py-2">{pct(assignment.membership_probability)}</td><td className="px-3 py-2">{assignment.dominant_shift}</td></tr>)}</tbody></table></div></div></div>}
    </div>
  );
}
