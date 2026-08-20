import ReactECharts from "echarts-for-react";
import { ChevronDown, ChevronRight, Download, Eye, FileCheck2, Play, RefreshCw, Split, Upload, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiForm, apiGet, apiSend, downloadFormFromApi, downloadFromApi } from "../lib/api";

type Depot = { depot_id: string; depot_name: string };
type Model = {
  model_id: string;
  model_name: string;
  model_version: number;
  depot_id: string;
  depot_name: string;
  training_start_date: string;
  training_end_date: string;
  created_at: string | null;
  algorithm: string;
  algorithm_version: string;
  number_of_training_shipments: number;
  number_of_spbu: number;
  number_of_clusters: number;
  model_quality_metrics: Record<string, number>;
  model_status: string;
};
type ValidationIssue = { file: string; row: number; field: string; status: string; error_code: string; description: string };
type Validation = {
  file_type: string;
  status: "PASS" | "WARNING" | "ERROR";
  blocking_error_count: number;
  warning_count: number;
  issues: ValidationIssue[];
  normalized_rows: Array<Record<string, string>>;
  row_count: number;
  detected_shifts: string[];
};
type Candidate = {
  id: string;
  vehicle_id: string;
  vehicle_registration_no: string;
  prediction_score: number;
  compatibility_status: string;
  candidate_rank: number | null;
  exclusion_reason: string | null;
  explanation: Record<string, unknown>;
};
type Shipment = {
  id: string;
  predicted_shipment_id: string;
  shift_id: string;
  shift: string;
  shipment_prediction_score: number;
  shipment_confidence_level: string;
  low_confidence: boolean;
  is_manual_override: boolean;
  explanation: Record<string, unknown>;
  lines: Array<{
    id: string;
    loading_order_no: string;
    spbu_id: string;
    spbu_no: string;
    spbu_name: string | null;
    model_predicted_shipment_id: string;
  }>;
  assignment: {
    id: string | null;
    original_vehicle_id: string | null;
    original_prediction_score: number | null;
    assigned_vehicle_id: string | null;
    assigned_vehicle_registration: string | null;
    mt_assignment_score: number | null;
    assignment_status: string;
    unassigned_reason: string | null;
    override_reason: string | null;
  };
  candidates: Candidate[];
};
type PredictionResult = {
  id: string;
  prediction_run_id: string;
  status: string;
  depot: string;
  model: Record<string, unknown>;
  created_at: string;
  created_by: string;
  parameters: Record<string, unknown>;
  validation: ValidationIssue[];
  durations_ms: Record<string, number>;
  summary: {
    loading_orders: number;
    unique_spbu: number;
    predicted_shipments: number;
    available_mt: number;
    assigned_shipments: number;
    unassigned_shipments: number;
    average_shipment_confidence: number;
    average_mt_assignment_confidence: number;
  };
  summary_by_shift: Array<Record<string, string | number>>;
  shipments: Shipment[];
  original_model_prediction: Array<Record<string, unknown>>;
};
type HistoryRow = {
  id: string;
  prediction_run_id: string;
  date: string;
  depot: string;
  model: string;
  loading_orders: number;
  shipments: number;
  assigned: number;
  unassigned: number;
  user: string;
  status: string;
};

function pct(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function badgeClass(value: string) {
  if (["PASS", "HIGH", "ASSIGNED", "COMPLETED", "ACTIVE", "READY"].includes(value)) return "border-mint bg-mint/10 text-mint";
  if (["WARNING", "MEDIUM", "MANUAL_OVERRIDE", "SAVED"].includes(value)) return "border-amber bg-amber/10 text-amber";
  return "border-rust bg-rust/10 text-rust";
}

function Badge({ value }: { value: string }) {
  return <span className={`inline-flex border px-2 py-1 text-[11px] font-semibold uppercase tracking-wide ${badgeClass(value)}`}>{value.replace(/_/g, " ")}</span>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="border border-line bg-white p-4">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-petroink">{value}</div>
    </div>
  );
}

function explanationRows(value: Record<string, unknown>) {
  return Object.entries(value).filter(([, item]) => item !== null && item !== undefined && typeof item !== "object");
}

export function PredictionAssignmentPage({ depots }: { depots: Depot[] }) {
  const [depotId, setDepotId] = useState("");
  const [models, setModels] = useState<Model[]>([]);
  const [modelId, setModelId] = useState("");
  const [loadingOrderFile, setLoadingOrderFile] = useState<File | null>(null);
  const [mtFile, setMtFile] = useState<File | null>(null);
  const [loValidation, setLoValidation] = useState<Validation | null>(null);
  const [mtValidation, setMtValidation] = useState<Validation | null>(null);
  const [result, setResult] = useState<PredictionResult | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [shiftTab, setShiftTab] = useState("ALL");
  const [overrideReason, setOverrideReason] = useState<Record<string, string>>({});
  const [moveTargets, setMoveTargets] = useState<Record<string, string>>({});
  const [minimumConfidence, setMinimumConfidence] = useState("0.60");

  const selectedModel = models.find((model) => model.model_id === modelId) ?? null;
  const issues = [...(loValidation?.issues ?? []), ...(mtValidation?.issues ?? [])];
  const blockingErrors = (loValidation?.blocking_error_count ?? 0) + (mtValidation?.blocking_error_count ?? 0);
  const warnings = (loValidation?.warning_count ?? 0) + (mtValidation?.warning_count ?? 0);
  const canRun = Boolean(depotId && modelId && loadingOrderFile && mtFile && loValidation && mtValidation && blockingErrors === 0);

  useEffect(() => {
    setModelId("");
    setModels([]);
    setLoadingOrderFile(null);
    setMtFile(null);
    setLoValidation(null);
    setMtValidation(null);
    setResult(null);
    if (!depotId) {
      setHistory([]);
      return;
    }
    Promise.all([
      apiGet<Model[]>(`/api/v1/phase6/models?depot_id=${encodeURIComponent(depotId)}`),
      apiGet<HistoryRow[]>(`/api/v1/phase6/predictions?depot_id=${encodeURIComponent(depotId)}`),
    ]).then(([modelRows, historyRows]) => {
      setModels(modelRows);
      setHistory(historyRows);
    }).catch((reason: Error) => setError(reason.message));
  }, [depotId]);

  useEffect(() => {
    setLoadingOrderFile(null);
    setMtFile(null);
    setLoValidation(null);
    setMtValidation(null);
    setResult(null);
  }, [modelId]);

  async function validateFile(kind: "loading-order" | "mt-availability", file: File) {
    if (!depotId || !modelId) return;
    setError(null);
    const body = new FormData();
    body.append("depot_id", depotId);
    body.append("model_id", modelId);
    body.append("file", file);
    try {
      const validation = await apiForm<Validation>(`/api/v1/phase6/validate/${kind}`, body);
      if (kind === "loading-order") setLoValidation(validation);
      else setMtValidation(validation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Validation failed.");
    }
  }

  async function runPrediction() {
    if (!canRun || !loadingOrderFile || !mtFile) return;
    setLoading(true);
    setError(null);
    const body = new FormData();
    body.append("depot_id", depotId);
    body.append("model_id", modelId);
    body.append("loading_order_file", loadingOrderFile);
    body.append("mt_availability_file", mtFile);
    body.append("parameters", JSON.stringify({ minimum_prediction_confidence: Number(minimumConfidence) }));
    try {
      const payload = await apiForm<PredictionResult>("/api/v1/phase6/predictions", body);
      setResult(payload);
      setExpanded(payload.shipments[0]?.id ?? null);
      setShiftTab("ALL");
      setHistory(await apiGet<HistoryRow[]>(`/api/v1/phase6/predictions?depot_id=${encodeURIComponent(depotId)}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Prediction failed.");
    } finally {
      setLoading(false);
    }
  }

  async function downloadValidation() {
    if (!loadingOrderFile || !mtFile) return;
    const body = new FormData();
    body.append("depot_id", depotId);
    body.append("model_id", modelId);
    body.append("loading_order_file", loadingOrderFile);
    body.append("mt_availability_file", mtFile);
    await downloadFormFromApi("/api/v1/phase6/validation-report", body, "phase6-validation-report.xlsx");
  }

  async function applyAssignmentOverride(shipment: Shipment, vehicleId: string) {
    if (!result || !shipment.assignment.id) return;
    setLoading(true);
    try {
      setResult(await apiSend<PredictionResult>(
        `/api/v1/phase6/predictions/${result.id}/assignments/${shipment.assignment.id}`,
        "PATCH",
        { vehicle_id: vehicleId, override_reason: overrideReason[shipment.id] || null },
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Override failed.");
    } finally {
      setLoading(false);
    }
  }

  async function adjustShipment(shipment: Shipment, action: string, lineIds: string[], targetShipmentId?: string) {
    if (!result) return;
    setLoading(true);
    try {
      setResult(await apiSend<PredictionResult>(
        `/api/v1/phase6/predictions/${result.id}/shipments/${shipment.id}`,
        "PATCH",
        { action, line_ids: lineIds, target_shipment_id: targetShipmentId || null },
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Shipment adjustment failed.");
    } finally {
      setLoading(false);
    }
  }

  async function openHistory(runId: string) {
    setLoading(true);
    try {
      const payload = await apiGet<PredictionResult>(`/api/v1/phase6/predictions/${runId}`);
      setResult(payload);
      setExpanded(payload.shipments[0]?.id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Run could not be opened.");
    } finally {
      setLoading(false);
    }
  }

  async function rerun(runId: string) {
    setLoading(true);
    try {
      const payload = await apiSend<PredictionResult>(`/api/v1/phase6/predictions/${runId}/recalculate`, "POST", { model_id: modelId || null });
      setResult(payload);
      setHistory(await apiGet<HistoryRow[]>(`/api/v1/phase6/predictions?depot_id=${encodeURIComponent(depotId)}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Re-run failed.");
    } finally {
      setLoading(false);
    }
  }

  const visibleShipments = useMemo(
    () => result?.shipments.filter((shipment) => shiftTab === "ALL" || shipment.shift_id === shiftTab) ?? [],
    [result, shiftTab],
  );
  const shifts = result?.summary_by_shift ?? [];
  const networkOption = useMemo(() => {
    const nodeMap = new Map<string, { id: string; name: string; value: string; category: number }>();
    const links: Array<{ source: string; target: string; value: number; lineStyle: { width: number } }> = [];
    visibleShipments.forEach((shipment, shipmentIndex) => {
      shipment.lines.forEach((line) => nodeMap.set(line.id, { id: line.id, name: line.spbu_no, value: shipment.predicted_shipment_id, category: shipmentIndex }));
      for (let left = 0; left < shipment.lines.length; left += 1) {
        for (let right = left + 1; right < shipment.lines.length; right += 1) {
          links.push({
            source: shipment.lines[left].id,
            target: shipment.lines[right].id,
            value: shipment.shipment_prediction_score,
            lineStyle: { width: Math.max(1, shipment.shipment_prediction_score * 7) },
          });
        }
      }
    });
    return {
      tooltip: { formatter: (item: { data: { name?: string; value?: string | number } }) => `${item.data.name ?? "Pair"}<br/>${item.data.value ?? ""}` },
      series: [{
        type: "graph",
        layout: "force",
        roam: true,
        label: { show: true },
        force: { repulsion: 220, edgeLength: 100 },
        data: [...nodeMap.values()],
        links,
        lineStyle: { color: "#0b73bf", opacity: 0.65 },
        itemStyle: { color: "#b8d211", borderColor: "#15385b", borderWidth: 1 },
      }],
    };
  }, [visibleShipments]);

  const matrixVehicles = useMemo(
    () => [...new Map(visibleShipments.flatMap((shipment) => shipment.candidates.map((candidate) => [candidate.vehicle_id, candidate.vehicle_registration_no]))).entries()],
    [visibleShipments],
  );

  return (
    <div className="space-y-5">
      {error && <div className="flex items-start justify-between border border-rust bg-rust/5 px-4 py-3 text-sm text-rust"><span>{error}</span><button onClick={() => setError(null)}><XCircle size={17} /></button></div>}

      <section className="border border-line bg-white p-5">
        <div className="mb-4">
          <div className="text-sm font-semibold uppercase tracking-wide text-slate-600">1. Prediction Setup</div>
          <p className="mt-1 text-xs text-slate-500">One depot and one saved Phase 5 model per auditable run. Phase 6 does not train models or optimize routes.</p>
        </div>
        <div className="grid gap-4 lg:grid-cols-3">
          <label className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Depot
            <select className="border border-line bg-white px-3 py-2 text-sm font-normal normal-case tracking-normal text-petroink" value={depotId} onChange={(event) => setDepotId(event.target.value)}>
              <option value="">Select Depot</option>
              {depots.map((depot) => <option key={depot.depot_id} value={depot.depot_id}>{depot.depot_name}</option>)}
            </select>
          </label>
          <label className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Prediction Model
            <select className="border border-line bg-white px-3 py-2 text-sm font-normal normal-case tracking-normal text-petroink" value={modelId} onChange={(event) => setModelId(event.target.value)} disabled={!depotId}>
              <option value="">Select READY / ACTIVE Model</option>
              {models.map((model) => <option key={model.model_id} value={model.model_id}>{model.model_name} · v{model.model_version} · {model.model_status}</option>)}
            </select>
          </label>
          <label className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Minimum Pairing Confidence
            <input className="border border-line px-3 py-2 text-sm font-normal normal-case tracking-normal text-petroink" type="number" min="0" max="1" step="0.05" value={minimumConfidence} onChange={(event) => setMinimumConfidence(event.target.value)} />
          </label>
        </div>
        {depotId && models.length === 0 && <div className="mt-4 border border-amber bg-amber/5 p-3 text-sm text-amber">No saved or active Phase 5 models are compatible with this depot.</div>}
      </section>

      {selectedModel && (
        <section className="border border-line bg-white p-5">
          <div className="mb-3 flex items-center justify-between"><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">2. Model Information</div><Badge value={selectedModel.model_status} /></div>
          <div className="grid gap-3 text-sm md:grid-cols-2 lg:grid-cols-4">
            {[
              ["Model ID", selectedModel.model_id], ["Model", `${selectedModel.model_name} v${selectedModel.model_version}`], ["Depot", selectedModel.depot_name],
              ["Training Period", `${selectedModel.training_start_date} — ${selectedModel.training_end_date}`], ["Created", selectedModel.created_at ?? "-"],
              ["Algorithm", selectedModel.algorithm], ["Training Shipments", selectedModel.number_of_training_shipments], ["SPBU", selectedModel.number_of_spbu],
              ["Clusters", selectedModel.number_of_clusters], ["Average Membership", pct(selectedModel.model_quality_metrics.average_membership_probability)],
            ].map(([label, value]) => <div key={String(label)}><div className="text-xs uppercase tracking-wide text-slate-500">{label}</div><div className="mt-1 break-words font-medium text-petroink">{value}</div></div>)}
          </div>
        </section>
      )}

      <section className="grid gap-5 lg:grid-cols-2">
        <div className="border border-line bg-white p-5">
          <div className="text-sm font-semibold uppercase tracking-wide text-slate-600">3. Loading Order Upload</div>
          <p className="mt-1 text-xs text-slate-500">Required: loading_order_no, shift_gate_out, spbu_no</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="inline-flex items-center gap-2 border border-line px-3 py-2 text-sm" onClick={() => downloadFromApi("/api/v1/phase6/templates/loading-order", "phase6-loading-order-template.xlsx")}><Download size={15} /> Download Template</button>
            <label className={`inline-flex cursor-pointer items-center gap-2 border px-3 py-2 text-sm ${modelId ? "border-petroblue text-petroblue" : "pointer-events-none border-line text-slate-400"}`}>
              <Upload size={15} /> {loadingOrderFile?.name ?? "Upload Excel"}
              <input className="hidden" type="file" accept=".xlsx" disabled={!modelId} onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                setLoadingOrderFile(file);
                setLoValidation(null);
                if (file) void validateFile("loading-order", file);
              }} />
            </label>
            {loValidation && <Badge value={loValidation.status} />}
          </div>
        </div>
        <div className="border border-line bg-white p-5">
          <div className="text-sm font-semibold uppercase tracking-wide text-slate-600">4. MT Availability Upload</div>
          <p className="mt-1 text-xs text-slate-500">Required: shift, vehicle_registration_no</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button className="inline-flex items-center gap-2 border border-line px-3 py-2 text-sm" onClick={() => downloadFromApi("/api/v1/phase6/templates/mt-availability", "phase6-mt-availability-template.xlsx")}><Download size={15} /> Download Template</button>
            <label className={`inline-flex cursor-pointer items-center gap-2 border px-3 py-2 text-sm ${modelId ? "border-petroblue text-petroblue" : "pointer-events-none border-line text-slate-400"}`}>
              <Upload size={15} /> {mtFile?.name ?? "Upload Excel"}
              <input className="hidden" type="file" accept=".xlsx" disabled={!modelId} onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                setMtFile(file);
                setMtValidation(null);
                if (file) void validateFile("mt-availability", file);
              }} />
            </label>
            {mtValidation && <Badge value={mtValidation.status} />}
          </div>
        </div>
      </section>

      {(loValidation || mtValidation) && (
        <section className="border border-line bg-white p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">5. Validation Result</div><p className="mt-1 text-xs text-slate-500">Prediction is blocked only by ERROR. WARNING remains reviewable.</p></div>
            <button className="inline-flex items-center gap-2 border border-line px-3 py-2 text-sm disabled:opacity-40" disabled={!loadingOrderFile || !mtFile} onClick={() => void downloadValidation()}><FileCheck2 size={15} /> Download Validation Report</button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
            <Metric label="Loading Orders" value={loValidation?.row_count ?? 0} />
            <Metric label="Unique SPBU" value={new Set((loValidation?.normalized_rows ?? []).map((row) => row.spbu_id)).size} />
            <Metric label="Available MT" value={mtValidation?.row_count ?? 0} />
            <Metric label="Detected Shifts" value={new Set([...(loValidation?.detected_shifts ?? []), ...(mtValidation?.detected_shifts ?? [])]).size} />
            <Metric label="Errors" value={blockingErrors} />
            <Metric label="Warnings" value={warnings} />
          </div>
          {issues.length > 0 ? (
            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full text-left text-sm"><thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr>{["File", "Row", "Field", "Status", "Error Code", "Description"].map((item) => <th key={item} className="px-3 py-2">{item}</th>)}</tr></thead>
                <tbody>{issues.map((issue, index) => <tr key={`${issue.file}-${issue.row}-${issue.error_code}-${index}`} className="border-t border-line"><td className="px-3 py-2">{issue.file}</td><td className="px-3 py-2">{issue.row}</td><td className="px-3 py-2">{issue.field}</td><td className="px-3 py-2"><Badge value={issue.status} /></td><td className="px-3 py-2 font-mono text-xs">{issue.error_code}</td><td className="px-3 py-2">{issue.description}</td></tr>)}</tbody>
              </table>
            </div>
          ) : <div className="mt-4 border border-mint bg-mint/5 p-3 text-sm text-mint">Both input files passed validation.</div>}
          <div className="mt-5 flex justify-end">
            <button className="inline-flex items-center gap-2 bg-petroblue px-5 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40" disabled={!canRun || loading} onClick={() => void runPrediction()}><Play size={16} /> {loading ? "Running Prediction…" : "Run Prediction"}</button>
          </div>
        </section>
      )}

      {!result && (
        <section className="border border-dashed border-line bg-white p-10 text-center">
          <div className="text-lg font-semibold text-petroink">Prediction workspace is empty</div>
          <p className="mx-auto mt-2 max-w-2xl text-sm text-slate-500">Select depot and prediction model, then upload Loading Order and MT Availability files to start prediction.</p>
        </section>
      )}

      {result && (
        <>
          <section className="border border-line bg-white p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">6. Prediction Summary · {result.prediction_run_id}</div><p className="mt-1 text-xs text-slate-500">Completed in {result.durations_ms.total} ms · model and input snapshots retained.</p></div><Badge value={result.status} /></div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
              <Metric label="Loading Orders" value={result.summary.loading_orders} /><Metric label="Unique SPBU" value={result.summary.unique_spbu} /><Metric label="Shipments" value={result.summary.predicted_shipments} /><Metric label="Available MT" value={result.summary.available_mt} />
              <Metric label="Assigned" value={result.summary.assigned_shipments} /><Metric label="Unassigned" value={result.summary.unassigned_shipments} /><Metric label="Avg Shipment" value={pct(result.summary.average_shipment_confidence)} /><Metric label="Avg MT" value={pct(result.summary.average_mt_assignment_confidence)} />
            </div>
            <div className="mt-4 overflow-x-auto"><table className="min-w-full text-left text-sm"><thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr>{["Shift", "LO", "SPBU", "Predicted Shipment", "Available MT", "Assigned", "Unassigned"].map((item) => <th key={item} className="px-3 py-2">{item}</th>)}</tr></thead><tbody>{result.summary_by_shift.map((row) => <tr key={String(row.shift_id)} className="border-t border-line"><td className="px-3 py-2 font-medium">{row.shift}</td><td className="px-3 py-2">{row.loading_orders}</td><td className="px-3 py-2">{row.unique_spbu}</td><td className="px-3 py-2">{row.predicted_shipments}</td><td className="px-3 py-2">{row.available_mt}</td><td className="px-3 py-2">{row.assigned}</td><td className="px-3 py-2">{row.unassigned}</td></tr>)}</tbody></table></div>
          </section>

          <section className="border border-line bg-white p-5">
            <div className="mb-4"><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">7–8. Predicted Shipment & MT Assignment Result</div><div className="mt-3 flex flex-wrap gap-2"><button className={`border px-3 py-1 text-sm ${shiftTab === "ALL" ? "border-petroblue bg-petroblue text-white" : "border-line"}`} onClick={() => setShiftTab("ALL")}>All</button>{shifts.map((shift) => <button key={String(shift.shift_id)} className={`border px-3 py-1 text-sm ${shiftTab === shift.shift_id ? "border-petroblue bg-petroblue text-white" : "border-line"}`} onClick={() => setShiftTab(String(shift.shift_id))}>{shift.shift}</button>)}</div></div>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm"><thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr>{["", "Shift", "Shipment", "Loading Order", "SPBU", "Assigned MT", "Shipment Confidence", "MT Confidence", "Status"].map((item, index) => <th key={`${item}-${index}`} className="px-3 py-2">{item}</th>)}</tr></thead>
                <tbody>{visibleShipments.map((shipment) => (
                  <>
                    <tr key={shipment.id} className="border-t border-line align-top">
                      <td className="px-3 py-3"><button onClick={() => setExpanded(expanded === shipment.id ? null : shipment.id)}>{expanded === shipment.id ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button></td>
                      <td className="px-3 py-3">{shipment.shift}</td><td className="px-3 py-3 font-mono text-xs">{shipment.predicted_shipment_id}</td><td className="px-3 py-3">{shipment.lines.map((line) => line.loading_order_no).join(" + ")}</td><td className="px-3 py-3">{shipment.lines.map((line) => line.spbu_no).join(" + ")}</td><td className="px-3 py-3">{shipment.assignment.assigned_vehicle_registration ?? "—"}</td><td className="px-3 py-3"><div>{pct(shipment.shipment_prediction_score)}</div><Badge value={shipment.shipment_confidence_level} /></td><td className="px-3 py-3">{pct(shipment.assignment.mt_assignment_score)}</td><td className="px-3 py-3"><Badge value={shipment.assignment.assignment_status} />{shipment.assignment.unassigned_reason && <div className="mt-1 text-xs text-rust">{shipment.assignment.unassigned_reason.replace(/_/g, " ")}</div>}</td>
                    </tr>
                    {expanded === shipment.id && (
                      <tr key={`${shipment.id}-detail`} className="border-t border-line bg-slate-50/60"><td colSpan={9} className="p-4">
                        <div className="grid gap-5 xl:grid-cols-2">
                          <div>
                            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Loading Orders & Shipment Override</div>
                            <div className="mt-2 space-y-2">{shipment.lines.map((line) => (
                              <div key={line.id} className="flex flex-wrap items-center justify-between gap-2 border border-line bg-white p-3 text-sm"><div><span className="font-medium">{line.loading_order_no}</span> · {line.spbu_no} {line.spbu_name && `· ${line.spbu_name}`}<div className="mt-1 text-[11px] text-slate-400">Model layer: {line.model_predicted_shipment_id}</div></div><div className="flex flex-wrap gap-2"><button className="inline-flex items-center gap-1 border border-line px-2 py-1 text-xs" disabled={shipment.lines.length === 1 || loading} onClick={() => void adjustShipment(shipment, "SPLIT_SINGLE", [line.id])}><Split size={13} /> New single</button><select className="border border-line bg-white px-2 py-1 text-xs" value={moveTargets[line.id] ?? ""} onChange={(event) => setMoveTargets((current) => ({ ...current, [line.id]: event.target.value }))}><option value="">Move to…</option>{result.shipments.filter((item) => item.shift_id === shipment.shift_id && item.id !== shipment.id).map((item) => <option key={item.id} value={item.id}>{item.predicted_shipment_id}</option>)}</select><button className="border border-line px-2 py-1 text-xs disabled:opacity-40" disabled={!moveTargets[line.id] || loading} onClick={() => void adjustShipment(shipment, "MOVE_LINES", [line.id], moveTargets[line.id])}>Move</button></div></div>
                            ))}</div>
                            <div className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Structured Shipment Explanation</div>
                            <dl className="mt-2 grid gap-2 sm:grid-cols-2">{explanationRows(shipment.explanation).map(([key, value]) => <div key={key} className="border border-line bg-white p-2"><dt className="text-[11px] uppercase text-slate-400">{key.replace(/_/g, " ")}</dt><dd className="mt-1 text-xs">{String(value)}</dd></div>)}</dl>
                          </div>
                          <div>
                            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">Recommended MT & Change MT</div>
                            <input className="mt-2 w-full border border-line bg-white px-3 py-2 text-sm" placeholder="Optional override reason" value={overrideReason[shipment.id] ?? ""} onChange={(event) => setOverrideReason((current) => ({ ...current, [shipment.id]: event.target.value }))} />
                            <div className="mt-2 overflow-x-auto"><table className="min-w-full bg-white text-left text-xs"><thead><tr className="border-b border-line text-slate-500"><th className="p-2">Rank</th><th className="p-2">MT</th><th className="p-2">Score</th><th className="p-2">Compatibility</th><th className="p-2">Action</th></tr></thead><tbody>{shipment.candidates.filter((candidate) => candidate.compatibility_status === "PASS").map((candidate) => <tr key={candidate.id} className="border-b border-line"><td className="p-2">{candidate.candidate_rank}</td><td className="p-2">{candidate.vehicle_registration_no}</td><td className="p-2">{pct(candidate.prediction_score)}</td><td className="p-2"><Badge value="PASS" /></td><td className="p-2"><button className="border border-petroblue px-2 py-1 text-petroblue disabled:opacity-40" disabled={candidate.vehicle_id === shipment.assignment.assigned_vehicle_id || loading} onClick={() => void applyAssignmentOverride(shipment, candidate.vehicle_id)}>Change MT</button></td></tr>)}</tbody></table></div>
                            {shipment.candidates.some((candidate) => candidate.compatibility_status === "FAIL") && <div className="mt-4"><div className="text-xs font-semibold uppercase tracking-wide text-rust">Excluded Candidate</div>{shipment.candidates.filter((candidate) => candidate.compatibility_status === "FAIL").map((candidate) => <div key={candidate.id} className="mt-2 border border-rust/30 bg-rust/5 p-2 text-xs"><span className="font-medium">{candidate.vehicle_registration_no}</span> · {candidate.exclusion_reason}</div>)}</div>}
                          </div>
                        </div>
                      </td></tr>
                    )}
                  </>
                ))}</tbody>
              </table>
            </div>
          </section>

          <section className="grid gap-5 xl:grid-cols-2">
            <div className="border border-line bg-white p-5"><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">9A. Shipment Prediction Network</div><p className="mt-1 text-xs text-slate-500">Nodes are SPBU; edges mean predicted same shipment; thickness is model confidence. This is not a route map.</p><ReactECharts option={networkOption} style={{ height: 360 }} /></div>
            <div className="border border-line bg-white p-5"><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">9B. MT Assignment Matrix</div><p className="mt-1 text-xs text-slate-500">Scores are Phase 4 affinity evidence after availability; X is master-incompatible; outlined cell is assigned.</p><div className="mt-4 max-h-[360px] overflow-auto"><table className="min-w-full text-center text-xs"><thead className="sticky top-0 bg-white"><tr><th className="p-2 text-left">Shipment</th>{matrixVehicles.map(([id, registration]) => <th key={id} className="p-2">{registration}</th>)}</tr></thead><tbody>{visibleShipments.map((shipment) => <tr key={shipment.id} className="border-t border-line"><th className="whitespace-nowrap p-2 text-left">{shipment.predicted_shipment_id}</th>{matrixVehicles.map(([vehicleId]) => {
              const candidate = shipment.candidates.find((item) => item.vehicle_id === vehicleId);
              const assigned = shipment.assignment.assigned_vehicle_id === vehicleId;
              return <td key={vehicleId} className={`p-2 ${assigned ? "outline outline-2 outline-petroblue" : ""}`} style={{ backgroundColor: candidate?.compatibility_status === "PASS" ? `rgba(184,210,17,${Math.max(0.08, candidate.prediction_score)})` : undefined }}>{candidate ? candidate.compatibility_status === "FAIL" ? "X" : pct(candidate.prediction_score) : "—"}</td>;
            })}</tr>)}</tbody></table></div></div>
          </section>
        </>
      )}

      <section className="border border-line bg-white p-5">
        <div className="mb-4 flex items-center justify-between"><div><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">10. Prediction Run History</div><p className="mt-1 text-xs text-slate-500">View and export immutable runs, or create a new run from an input snapshot.</p></div>{depotId && <button className="inline-flex items-center gap-2 border border-line px-3 py-2 text-sm" onClick={() => apiGet<HistoryRow[]>(`/api/v1/phase6/predictions?depot_id=${encodeURIComponent(depotId)}`).then(setHistory)}><RefreshCw size={14} /> Refresh</button>}</div>
        {history.length === 0 ? <div className="border border-dashed border-line p-6 text-center text-sm text-slate-500">No prediction runs for the selected depot.</div> : <div className="overflow-x-auto"><table className="min-w-full text-left text-sm"><thead className="bg-slate-50 text-xs uppercase text-slate-500"><tr>{["Run ID", "Date", "Depot", "Model", "LO", "Shipment", "Assigned", "Unassigned", "User", "Actions"].map((item) => <th key={item} className="px-3 py-2">{item}</th>)}</tr></thead><tbody>{history.map((row) => <tr key={row.id} className="border-t border-line"><td className="px-3 py-2 font-mono text-xs">{row.prediction_run_id}</td><td className="px-3 py-2">{new Date(row.date).toLocaleString()}</td><td className="px-3 py-2">{row.depot}</td><td className="px-3 py-2">{row.model}</td><td className="px-3 py-2">{row.loading_orders}</td><td className="px-3 py-2">{row.shipments}</td><td className="px-3 py-2">{row.assigned}</td><td className="px-3 py-2">{row.unassigned}</td><td className="px-3 py-2">{row.user}</td><td className="px-3 py-2"><div className="flex gap-2"><button title="View" className="border border-line p-2" onClick={() => void openHistory(row.id)}><Eye size={14} /></button><button title="Download" className="border border-line p-2" onClick={() => downloadFromApi(`/api/v1/phase6/predictions/${row.id}/export`, `${row.prediction_run_id}.xlsx`)}><Download size={14} /></button><button title="Duplicate / Re-run" className="border border-line p-2" onClick={() => void rerun(row.id)}><RefreshCw size={14} /></button></div></td></tr>)}</tbody></table></div>}
      </section>

      <section className="border border-line bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">11. Export Result</div><p className="mt-1 text-xs text-slate-500">Summary, Shipment Result, MT Assignment, MT Candidates, and Validation sheets.</p></div><button className="inline-flex items-center gap-2 bg-petroblue px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={!result} onClick={() => result && downloadFromApi(`/api/v1/phase6/predictions/${result.id}/export`, `${result.prediction_run_id}.xlsx`)}><Download size={16} /> Export Prediction Result</button></div>
      </section>
    </div>
  );
}
