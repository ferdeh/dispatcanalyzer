import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  AlertTriangle,
  ArrowUpDown,
  Boxes,
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Database,
  Gauge,
  GitCompareArrows,
  History,
  LoaderCircle,
  MapPinned,
  Play,
  Plus,
  RefreshCw,
  Route,
  Save,
  Search,
  Settings2,
  Trash2,
  Truck,
  Warehouse,
  X,
} from "lucide-react";
import { apiGet, apiSend } from "../lib/api";


type Depot = { depot_id: string; depot_code: string | null; depot_name: string };
type Product = { product_id: string; product_name: string; active_status?: string };
type Phase7Tab = "overview" | "lo" | "mt" | "bay" | "parameter" | "route" | "simulation" | "map" | "cost" | "versions";
type JobSortKey = "job_no" | "operating_date" | "depot" | "total_lo" | "total_mt" | "current_route_version" | "status" | "last_updated";
type LOSortKey = "loading_order_id" | "spbu" | "product" | "volume_kl" | "phase6_shipment" | "phase6_mt" | "current_mt" | "trip" | "planned_gate_out" | "status" | "frozen";
type MTSortKey = "registration" | "class_tags" | "capacity_kl" | "compartments" | "planned_eta" | "system_eta" | "user_eta" | "effective_eta" | "status" | "working_time";
type JobSummary = {
  job_id: string;
  job_no: string;
  job_name: string;
  operating_date: string;
  depot_id: string;
  depot: string;
  total_lo: number;
  total_mt: number;
  current_route_version_id: string | null;
  current_route_version: string | null;
  status: string;
  last_updated: string;
};
type OptimizationDispatch = {
  job_id: string;
  job_no: string;
  status: "CALCULATING";
  run_type: "INITIAL" | "REROUTE";
  optimization_reference_time: string;
  message: string;
};
type JobDetail = JobSummary & {
  header: Record<string, string | null>;
  kpis: Record<string, number>;
  source_prediction_run_id: string | null;
  depot_operational_start: string;
  depot_operational_end: string;
  depot_timezone: string;
  initial_optimization_reference_time: string | null;
  latest_optimization_reference_time: string | null;
  error_message?: string | null;
  optimization?: {
    run_id: string;
    run_type: string;
    status: string;
    solver_status: string;
    stage: string;
    progress_pct: number;
    elapsed_ms: number;
    started_at: string | null;
    ended_at: string | null;
    optimization_reference_time: string | null;
    metadata: Record<string, unknown>;
    error_code: string | null;
    error_message: string | null;
  } | null;
};
type PredictionRun = {
  id: string;
  run_id: string;
  date: string;
  depot: string;
  total_lo: number;
  predicted_shipment_count: number;
  predicted_mt_count: number;
  model_name: string;
  model_id: string;
  saved_at: string;
  status: string;
};
type LoadingOrder = {
  loading_order_id: string;
  spbu_id: string;
  spbu_name: string | null;
  product_id: string | null;
  product_name: string | null;
  volume_kl: number;
  phase6_shipment: string | null;
  phase6_mt: string | null;
  current_mt: string | null;
  current_trip: number | null;
  current_compartment: string | null;
  planned_gate_out: string | null;
  status: "PLANNED" | "ONGOING" | "DONE";
  frozen: boolean;
  frozen_reason: string | null;
};
type Vehicle = {
  mt_id: string;
  registration: string | null;
  vehicle_class: number | null;
  tags: string[];
  capacity_kl: number;
  number_of_compartments: number;
  compartments: Array<{ compartment_id: string; capacity_kl: number }>;
  planned_eta_depot: string | null;
  system_eta_depot: string | null;
  user_eta_override: string | null;
  effective_eta_depot: string | null;
  operational_status: string;
  working_time_used: number;
  working_time_remaining: number;
  working_time_limit: number;
};
type ParameterProfile = {
  profile_id: string;
  profile_name: string;
  description: string | null;
  version: number;
  is_default: boolean;
  parameters: Record<string, unknown>;
};
type ConstraintDefinition = {
  constraint_id: string;
  label: string;
  category: string;
  description: string;
  default_mode: "HARD" | "SOFT";
  default_penalty: number;
  default_limit_minutes?: number;
};
type ConstraintRule = {
  enabled: boolean;
  mode: "HARD" | "SOFT";
  penalty: number;
  limit_minutes?: number;
};
type RouteTrip = {
  route_version_trip_id: string;
  vehicle_id: string;
  registration: string | null;
  trip_number: number;
  shipment_id: string;
  vehicle_ready_at_depot: string;
  queue_start: string | null;
  loading_start: string | null;
  loading_finish: string | null;
  gate_out: string;
  return_depot: string;
  distance_meters: number;
  travel_time_seconds: number;
  queue_minutes: number;
  loading_minutes: number;
  operating_minutes: number;
  assignment_status: string;
  route_geometry: Array<{ latitude: number; longitude: number }>;
  route_geometry_source: string | null;
  cost_breakdown: Record<string, unknown>;
  stops: Array<{
    sequence: number;
    spbu_id: string;
    spbu_name: string | null;
    latitude: number | null;
    longitude: number | null;
    arrival_time: string;
    departure_time: string;
    volume_kl: number;
    loading_order_ids: string[];
    products: Array<string | null>;
    product_names: string[];
    distance_from_previous_meters: number;
    travel_from_previous_seconds: number;
  }>;
  loading_orders: Array<{
    loading_order_id: string;
    spbu_id: string;
    spbu_name: string | null;
    product_id: string | null;
    product_name: string | null;
    volume_kl: number;
    compartment_id: string | null;
    stop_sequence: number;
    eta: string;
    frozen: boolean;
    status: string;
  }>;
};
type RouteVersion = {
  route_version_id: string;
  version_number: number;
  version_label: string;
  created_at: string;
  created_by: string;
  reason: string;
  objective: string;
  solver_status: string;
  optimization_reference_time: string | null;
  first_loading_start: string | null;
  first_gate_out: string | null;
  last_gate_out: string | null;
  depot_dispatch_span_minutes: number;
  summary: Record<string, number | string | null>;
  cost: Record<string, number>;
  comparison: Record<string, number | string | boolean>;
  audit_events: Array<Record<string, unknown>>;
  parameter_checksum: string | null;
  parameter_snapshot: Record<string, unknown>;
  trips: RouteTrip[];
  dropped_lo: Array<Record<string, string | number | null>>;
};
type BayPayload = {
  configuration: {
    number_of_bays: number;
    bays: Array<Record<string, unknown>>;
    loading_durations: Array<Record<string, unknown>>;
  };
  states: Array<Record<string, unknown>>;
  queue: Array<Record<string, unknown>>;
};
type OptimizationDialog = {
  reroute: boolean;
  date: string;
  time: string;
};


const tabs: Array<{ id: Phase7Tab; label: string; icon: typeof Route }> = [
  { id: "overview", label: "Job Overview", icon: Gauge },
  { id: "lo", label: "LO Management", icon: Boxes },
  { id: "mt", label: "MT Management", icon: Truck },
  { id: "bay", label: "Bay Management", icon: Warehouse },
  { id: "parameter", label: "Parameter", icon: Settings2 },
  { id: "route", label: "Route Plan", icon: Route },
  { id: "simulation", label: "Simulation", icon: CalendarClock },
  { id: "map", label: "Geographic Map", icon: MapPinned },
  { id: "cost", label: "Cost & Dropped LO", icon: CircleDollarSign },
  { id: "versions", label: "Versions / Audit", icon: History },
];


function displayDateTime(value: string | null | undefined, timeZone?: string): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("id-ID", { dateStyle: "medium", timeStyle: "short", timeZone }).format(new Date(value));
}


function toLocalInput(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}


function zonedDateTimeParts(value: string | Date, timeZone: string): { date: string; time: string } {
  const date = value instanceof Date ? value : new Date(value);
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const read = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value || "";
  return { date: `${read("year")}-${read("month")}-${read("day")}`, time: `${read("hour")}:${read("minute")}` };
}


function badgeClass(status: string): string {
  if (["ACTIVE", "COMPLETED", "DONE", "READY", "OPTIMAL", "FEASIBLE"].includes(status)) return "phase7-badge is-good";
  if (["FAILED", "INFEASIBLE", "DROPPED", "BLOCKED"].includes(status)) return "phase7-badge is-bad";
  if (["ONGOING", "CALCULATING", "PARTIAL", "WARNING"].includes(status)) return "phase7-badge is-warning";
  return "phase7-badge";
}


function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="phase7-empty">
      <Database size={28} />
      <strong>{title}</strong>
      <span>{description}</span>
    </div>
  );
}


function Section({ title, description, action, children }: { title: string; description?: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="phase7-card">
      <div className="phase7-section-head">
        <div><h3>{title}</h3>{description && <p>{description}</p>}</div>
        {action}
      </div>
      {children}
    </section>
  );
}


function KpiGrid({ values }: { values: Array<{ label: string; value: string | number; hint?: string }> }) {
  return (
    <div className="phase7-kpi-grid">
      {values.map((item) => (
        <div className="phase7-kpi" key={item.label}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          {item.hint && <small>{item.hint}</small>}
        </div>
      ))}
    </div>
  );
}


function OperationalKpiGroups({ routeVersion }: { routeVersion: RouteVersion }) {
  return (
    <Section title="Operational KPI Groups" description="Complete version-aware LO, fleet, multi-trip, bay, route, cost, and reroute health indicators.">
      <div className="phase7-kpi-groups">
        <div><h4>LO</h4><KpiGrid values={[{ label: "Total LO", value: routeVersion.summary.total_lo || 0 }, { label: "Done LO", value: routeVersion.summary.done_lo || 0 }, { label: "Ongoing LO", value: routeVersion.summary.ongoing_lo || 0 }, { label: "Planned LO", value: routeVersion.summary.planned_lo || 0 }, { label: "Dropped LO", value: routeVersion.summary.dropped_lo || 0 }, { label: "Completion", value: `${routeVersion.summary.completion_pct || 0}%` }, { label: "Delivered", value: `${routeVersion.summary.delivered_kl || 0} KL` }, { label: "Remaining", value: `${routeVersion.summary.remaining_kl || 0} KL` }]} /></div>
        <div><h4>Fleet</h4><KpiGrid values={[{ label: "Available MT", value: routeVersion.summary.available_mt || 0 }, { label: "Used MT", value: routeVersion.summary.used_mt || 0 }, { label: "Unused MT", value: routeVersion.summary.unused_mt || 0 }, { label: "Utilization", value: `${routeVersion.summary.fleet_utilization_pct || 0}%` }, { label: "Working Used", value: `${routeVersion.summary.working_time_used_minutes || 0} min` }, { label: "Working Remaining", value: `${routeVersion.summary.working_time_remaining_minutes || 0} min` }]} /></div>
        <div><h4>Multi-Trip</h4><KpiGrid values={[{ label: "Average Trips / MT", value: routeVersion.summary.average_trips_per_mt || 0 }, { label: "Maximum Trips", value: routeVersion.summary.max_trips_per_mt || 0 }, { label: "MT 1 Trip", value: routeVersion.summary.mt_with_1_trip || 0 }, { label: "MT 2 Trips", value: routeVersion.summary.mt_with_2_trips || 0 }, { label: "MT 3+ Trips", value: routeVersion.summary.mt_with_3_plus_trips || 0 }, { label: "Avg Turnaround", value: `${routeVersion.summary.average_turnaround_minutes || 0} min` }]} /></div>
        <div><h4>Bay</h4><KpiGrid values={[{ label: "Average Queue", value: `${routeVersion.summary.average_queue_minutes || 0} min` }, { label: "Maximum Queue", value: `${routeVersion.summary.maximum_queue_minutes || 0} min` }, { label: "Queue Length", value: routeVersion.summary.queue_length || 0 }, { label: "Bay Utilization", value: `${routeVersion.summary.bay_utilization_pct || 0}%` }, { label: "Bay Idle", value: `${routeVersion.summary.bay_idle_minutes || 0} min` }, { label: "Throughput", value: `${routeVersion.summary.loading_throughput_kl_per_hour || 0} KL/h` }, { label: "Bottleneck", value: String(routeVersion.summary.bay_bottleneck || "—") }]} /></div>
        <div><h4>Route</h4><KpiGrid values={[{ label: "Total Distance", value: `${(Number(routeVersion.summary.total_distance_meters || 0) / 1000).toFixed(1)} km` }, { label: "Travel Time", value: `${Math.round(Number(routeVersion.summary.total_travel_time_seconds || 0) / 60)} min` }, { label: "Operating Time", value: `${routeVersion.summary.total_operating_minutes || 0} min` }, { label: "Total Trips", value: routeVersion.summary.total_trips || 0 }, { label: "Average Trip", value: `${routeVersion.summary.average_trip_duration_minutes || 0} min` }]} /></div>
        <div><h4>Cost</h4><KpiGrid values={[{ label: "Total Cost", value: `Rp ${Number(routeVersion.summary.total_cost || 0).toLocaleString("id-ID")}` }, { label: "Cost / KL", value: `Rp ${Number(routeVersion.summary.cost_per_kl || 0).toLocaleString("id-ID")}` }, { label: "Cost / Trip", value: `Rp ${Number(routeVersion.summary.cost_per_trip || 0).toLocaleString("id-ID")}` }, { label: "Cost / MT", value: `Rp ${Number(routeVersion.cost.cost_per_mt || 0).toLocaleString("id-ID")}` }, { label: "Activation Cost", value: `Rp ${Number(routeVersion.summary.activation_cost || 0).toLocaleString("id-ID")}` }, { label: "Distance Cost", value: `Rp ${Number(routeVersion.summary.distance_cost || 0).toLocaleString("id-ID")}` }]} /></div>
        <div><h4>Reoptimization</h4><KpiGrid values={[{ label: "Reroutes", value: routeVersion.summary.reroute_number || 0 }, { label: "LO Reassigned", value: routeVersion.summary.lo_reassigned || 0 }, { label: "Shipment Regrouped", value: routeVersion.summary.shipment_regrouped || 0 }, { label: "MT Changes", value: routeVersion.summary.mt_assignment_changes || 0 }, { label: "Gate-Out Changes", value: routeVersion.summary.gate_out_changes || 0 }, { label: "Plan Stability", value: `${routeVersion.summary.plan_stability_pct || 0}%` }]} /></div>
      </div>
    </Section>
  );
}


export function Phase7OptimizationPage({ depots, products }: { depots: Depot[]; products: Product[] }) {
  const [selectedDepot, setSelectedDepot] = useState("");
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobSearch, setJobSearch] = useState("");
  const [jobSort, setJobSort] = useState<{ key: JobSortKey; direction: "asc" | "desc" }>({ key: "last_updated", direction: "desc" });
  const [jobPage, setJobPage] = useState(1);
  const [jobsPerPage, setJobsPerPage] = useState(10);
  const [deleteJobTarget, setDeleteJobTarget] = useState<JobSummary | null>(null);
  const [deleteBayTarget, setDeleteBayTarget] = useState<Record<string, unknown> | null>(null);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [tab, setTab] = useState<Phase7Tab>("overview");
  const [loadingOrders, setLoadingOrders] = useState<LoadingOrder[]>([]);
  const [loSearch, setLOSearch] = useState("");
  const [loSort, setLOSort] = useState<{ key: LOSortKey; direction: "asc" | "desc" }>({ key: "loading_order_id", direction: "asc" });
  const [loPage, setLOPage] = useState(1);
  const [loPerPage, setLOPerPage] = useState(25);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [mtSearch, setMTSearch] = useState("");
  const [mtSort, setMTSort] = useState<{ key: MTSortKey; direction: "asc" | "desc" }>({ key: "registration", direction: "asc" });
  const [mtPage, setMTPage] = useState(1);
  const [mtPerPage, setMTPerPage] = useState(25);
  const [predictionRuns, setPredictionRuns] = useState<PredictionRun[]>([]);
  const [selectedPredictionRun, setSelectedPredictionRun] = useState("");
  const [profiles, setProfiles] = useState<ParameterProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [parameterDraft, setParameterDraft] = useState<Record<string, unknown>>({});
  const [constraintCatalog, setConstraintCatalog] = useState<ConstraintDefinition[]>([]);
  const [versions, setVersions] = useState<Array<Record<string, unknown>>>([]);
  const [selectedVersion, setSelectedVersion] = useState("");
  const [routeVersion, setRouteVersion] = useState<RouteVersion | null>(null);
  const [bay, setBay] = useState<BayPayload | null>(null);
  const [selectedLO, setSelectedLO] = useState<Set<string>>(new Set());
  const [bulkLOStatus, setBulkLOStatus] = useState<LoadingOrder["status"]>("ONGOING");
  const [vehicleDrafts, setVehicleDrafts] = useState<Record<string, { planned: string; override: string; status: string }>>({});
  const [plannedETADemoDialog, setPlannedETADemoDialog] = useState<{ date: string; time: string } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [optimizationDialog, setOptimizationDialog] = useState<OptimizationDialog | null>(null);
  const [createForm, setCreateForm] = useState({ operating_date: new Date().toISOString().slice(0, 10), job_name: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [validation, setValidation] = useState<{ status: string; messages: Array<{ code: string; level: string; message: string }> } | null>(null);
  const [readinessOpen, setReadinessOpen] = useState(false);
  const [selectedMT, setSelectedMT] = useState("");
  const [selectedTrip, setSelectedTrip] = useState<number | "ALL">("ALL");
  const [routeMTPage, setRouteMTPage] = useState(1);
  const [routeMTPerPage, setRouteMTPerPage] = useState(5);
  const [routeLOFilter, setRouteLOFilter] = useState("");
  const [routeSPBUFilter, setRouteSPBUFilter] = useState("");
  const [routeProductFilter, setRouteProductFilter] = useState("");

  async function loadJobs(depotId: string) {
    if (!depotId) { setJobs([]); return; }
    try { setJobs(await apiGet<JobSummary[]>(`/api/v1/phase7/jobs?depot_id=${encodeURIComponent(depotId)}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load Phase 7 jobs."); }
  }

  async function loadWorkspace(jobId: string, versionId?: string) {
    const [jobPayload, loPayload, vehiclePayload, runPayload, profilePayload, constraintPayload, versionPayload, bayPayload] = await Promise.all([
      apiGet<JobDetail>(`/api/v1/phase7/jobs/${jobId}`),
      apiGet<LoadingOrder[]>(`/api/v1/phase7/jobs/${jobId}/loading-orders`),
      apiGet<Vehicle[]>(`/api/v1/phase7/jobs/${jobId}/vehicles`),
      apiGet<PredictionRun[]>(`/api/v1/phase7/jobs/${jobId}/prediction-runs`),
      apiGet<ParameterProfile[]>("/api/v1/phase7/parameter-profiles"),
      apiGet<ConstraintDefinition[]>("/api/v1/phase7/constraint-catalog"),
      apiGet<Array<Record<string, unknown>>>(`/api/v1/phase7/jobs/${jobId}/versions`),
      apiGet<BayPayload>(`/api/v1/phase7/jobs/${jobId}/bay-state`),
    ]);
    setJob(jobPayload);
    setLoadingOrders(loPayload);
    setVehicles(vehiclePayload);
    setPredictionRuns(runPayload);
    setProfiles(profilePayload);
    setConstraintCatalog(constraintPayload);
    setVersions(versionPayload);
    setBay(bayPayload);
    setSelectedDepot(jobPayload.depot_id);
    setSelectedProfile((current) => current || profilePayload.find((row) => row.is_default)?.profile_id || profilePayload[0]?.profile_id || "");
    const currentProfile = profilePayload.find((row) => row.profile_id === selectedProfile) || profilePayload.find((row) => row.is_default) || profilePayload[0];
    if (currentProfile) setParameterDraft(currentProfile.parameters);
    setVehicleDrafts(Object.fromEntries(vehiclePayload.map((row) => [row.mt_id, { planned: toLocalInput(row.planned_eta_depot), override: toLocalInput(row.user_eta_override), status: row.operational_status }])));
    const targetVersion = versionId || jobPayload.current_route_version_id || "";
    setSelectedVersion(targetVersion);
    if (targetVersion) setRouteVersion(await apiGet<RouteVersion>(`/api/v1/phase7/jobs/${jobId}/versions/${targetVersion}`));
    else setRouteVersion(null);
  }

  useEffect(() => { void loadJobs(selectedDepot); }, [selectedDepot]);

  const calculatingJobsKey = jobs
    .filter((row) => row.status === "CALCULATING")
    .map((row) => row.job_id)
    .sort()
    .join(",");

  useEffect(() => {
    if (!selectedDepot || job || !calculatingJobsKey) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await apiGet<JobSummary[]>(`/api/v1/phase7/jobs?depot_id=${encodeURIComponent(selectedDepot)}`);
        if (!cancelled) setJobs(payload);
      } catch {
        // Keep the current table visible during a transient polling failure.
      }
    };
    const timer = window.setInterval(() => void poll(), 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [selectedDepot, Boolean(job), calculatingJobsKey]);

  useEffect(() => {
    const profile = profiles.find((row) => row.profile_id === selectedProfile);
    if (profile) setParameterDraft(profile.parameters);
  }, [selectedProfile, profiles]);

  useEffect(() => {
    if (!job || job.status !== "CALCULATING") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await apiGet<JobDetail>(`/api/v1/phase7/jobs/${job.job_id}`);
        if (!cancelled) setJob(payload);
      } catch {
        // The original optimize request remains authoritative. A transient
        // progress-poll failure must not replace its eventual result/error.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [job?.job_id, job?.status]);

  async function runAction(action: () => Promise<void>, success: string): Promise<boolean> {
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); return true; }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Phase 7 action failed."); return false; }
    finally { setBusy(false); }
  }

  async function openJob(jobId: string) {
    setValidation(null);
    setReadinessOpen(false);
    await runAction(async () => { await loadWorkspace(jobId); setTab("overview"); }, "Job workspace loaded.");
  }

  const selectedRun = predictionRuns.find((row) => row.id === selectedPredictionRun || row.run_id === selectedPredictionRun);
  const selectedProfileRow = profiles.find((row) => row.profile_id === selectedProfile);
  const routeVehicleOptions = useMemo(() => Array.from(new Map((routeVersion?.trips || []).map((row) => [row.vehicle_id, row.registration || row.vehicle_id])).entries()).sort((left, right) => left[1].localeCompare(right[1], "id-ID", { numeric: true, sensitivity: "base" })), [routeVersion]);
  const routeFilteredVehicleOptions = useMemo(() => selectedMT ? routeVehicleOptions.filter(([vehicleId]) => vehicleId === selectedMT) : routeVehicleOptions, [routeVehicleOptions, selectedMT]);
  const routeMTPageCount = Math.max(1, Math.ceil(routeFilteredVehicleOptions.length / routeMTPerPage));
  const pagedRouteVehicleOptions = routeFilteredVehicleOptions.slice((routeMTPage - 1) * routeMTPerPage, routeMTPage * routeMTPerPage);
  const routeMTRangeStart = routeFilteredVehicleOptions.length ? (routeMTPage - 1) * routeMTPerPage + 1 : 0;
  const routeMTRangeEnd = Math.min(routeMTPage * routeMTPerPage, routeFilteredVehicleOptions.length);
  const visibleRouteVehicleIds = useMemo(() => new Set(pagedRouteVehicleOptions.map(([vehicleId]) => vehicleId)), [pagedRouteVehicleOptions]);
  const loMatchesRouteFilters = (row: RouteTrip["loading_orders"][number]) =>
    (!routeLOFilter || row.loading_order_id.toLocaleLowerCase("id-ID").includes(routeLOFilter.toLocaleLowerCase("id-ID")))
    && (!routeSPBUFilter || `${row.spbu_id} ${row.spbu_name || ""}`.toLocaleLowerCase("id-ID").includes(routeSPBUFilter.toLocaleLowerCase("id-ID")))
    && (!routeProductFilter || `${row.product_name || ""} ${row.product_id || ""}`.toLocaleLowerCase("id-ID").includes(routeProductFilter.toLocaleLowerCase("id-ID")));
  const selectedTrips = useMemo(() => (routeVersion?.trips || []).filter((trip) =>
    (!selectedMT || trip.vehicle_id === selectedMT)
    && (selectedTrip === "ALL" || trip.trip_number === selectedTrip)),
  [routeVersion, selectedMT, selectedTrip]);
  const filteredTrips = useMemo(() => selectedTrips.filter((trip) =>
    visibleRouteVehicleIds.has(trip.vehicle_id)
    && ((!routeLOFilter && !routeSPBUFilter && !routeProductFilter) || trip.loading_orders.some(loMatchesRouteFilters)),
  ), [selectedTrips, visibleRouteVehicleIds, routeLOFilter, routeSPBUFilter, routeProductFilter]);
  const costByMT = useMemo(() => {
    const grouped = new Map<string, { registration: string; trips: number; distance: number; operating: number; volume: number; cost: number }>();
    for (const trip of routeVersion?.trips || []) {
      const current = grouped.get(trip.vehicle_id) || { registration: trip.registration || trip.vehicle_id, trips: 0, distance: 0, operating: 0, volume: 0, cost: 0 };
      current.trips += 1;
      current.distance += trip.distance_meters;
      current.operating += trip.operating_minutes;
      current.volume += trip.loading_orders.reduce((sum, row) => sum + row.volume_kl, 0);
      current.cost += Number(trip.cost_breakdown?.total_cost || 0);
      grouped.set(trip.vehicle_id, current);
    }
    return Array.from(grouped.entries());
  }, [routeVersion]);
  const tripsByMT = useMemo(() => pagedRouteVehicleOptions.map(([vehicleId, registration]) => [vehicleId, {
    registration,
    trips: (routeVersion?.trips || []).filter((trip) => trip.vehicle_id === vehicleId).sort((left, right) => left.trip_number - right.trip_number),
  }] as const), [pagedRouteVehicleOptions, routeVersion]);
  const hourlySimulation = useMemo(() => {
    const hours = new Map<number, { gateOutKL: number; returningMT: number; returningCapacityKL: number }>();
    const ensureHour = (timestamp: number) => {
      if (!hours.has(timestamp)) hours.set(timestamp, { gateOutKL: 0, returningMT: 0, returningCapacityKL: 0 });
      return hours.get(timestamp)!;
    };
    for (const trip of routeVersion?.trips || []) {
      const gateOut = new Date(trip.gate_out); gateOut.setMinutes(0, 0, 0);
      ensureHour(gateOut.getTime()).gateOutKL += trip.loading_orders.reduce((sum, row) => sum + row.volume_kl, 0);
      const returned = new Date(trip.return_depot); returned.setMinutes(0, 0, 0);
      const bucket = ensureHour(returned.getTime());
      bucket.returningMT += 1;
      bucket.returningCapacityKL += vehicles.find((row) => row.mt_id === trip.vehicle_id)?.capacity_kl || 0;
    }
    const timestamps = Array.from(hours.keys()).sort((a, b) => a - b);
    if (timestamps.length) {
      for (let timestamp = timestamps[0]; timestamp <= timestamps[timestamps.length - 1]; timestamp += 3_600_000) ensureHour(timestamp);
    }
    let cumulativeKL = 0;
    return Array.from(hours.entries()).sort(([a], [b]) => a - b).map(([timestamp, row]) => ({
      label: new Date(timestamp).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" }),
      ...row,
      cumulativeKL: cumulativeKL += row.gateOutKL,
    }));
  }, [routeVersion, vehicles]);

  const filteredJobs = useMemo(() => {
    const query = jobSearch.trim().toLocaleLowerCase("id-ID");
    const rows = query ? jobs.filter((row) => [
      row.job_no,
      row.job_name,
      row.operating_date,
      row.depot,
      row.current_route_version || "",
      row.status,
    ].some((value) => value.toLocaleLowerCase("id-ID").includes(query))) : jobs;
    return [...rows].sort((left, right) => {
      const leftValue = left[jobSort.key] ?? "";
      const rightValue = right[jobSort.key] ?? "";
      const comparison = typeof leftValue === "number" && typeof rightValue === "number"
        ? leftValue - rightValue
        : String(leftValue).localeCompare(String(rightValue), "id-ID", { numeric: true, sensitivity: "base" });
      return jobSort.direction === "asc" ? comparison : -comparison;
    });
  }, [jobSearch, jobSort, jobs]);
  const jobPageCount = Math.max(1, Math.ceil(filteredJobs.length / jobsPerPage));
  const pagedJobs = filteredJobs.slice((jobPage - 1) * jobsPerPage, jobPage * jobsPerPage);
  const jobRangeStart = filteredJobs.length ? (jobPage - 1) * jobsPerPage + 1 : 0;
  const jobRangeEnd = Math.min(jobPage * jobsPerPage, filteredJobs.length);

  const filteredLO = useMemo(() => {
    const query = loSearch.trim().toLocaleLowerCase("id-ID");
    const rows = query ? loadingOrders.filter((row) => [row.loading_order_id, row.spbu_id, row.spbu_name, row.product_id, row.product_name, row.phase6_shipment, row.phase6_mt, row.current_mt, row.current_compartment, row.status, row.frozen_reason].some((value) => String(value || "").toLocaleLowerCase("id-ID").includes(query))) : loadingOrders;
    const value = (row: LoadingOrder): string | number => ({
      loading_order_id: row.loading_order_id,
      spbu: row.spbu_name || row.spbu_id,
      product: row.product_name || row.product_id || "",
      volume_kl: row.volume_kl,
      phase6_shipment: row.phase6_shipment || "",
      phase6_mt: row.phase6_mt || "",
      current_mt: row.current_mt || "",
      trip: row.current_trip || 0,
      planned_gate_out: row.planned_gate_out || "",
      status: row.status,
      frozen: row.frozen ? 1 : 0,
    })[loSort.key];
    return [...rows].sort((left, right) => {
      const leftValue = value(left); const rightValue = value(right);
      const comparison = typeof leftValue === "number" && typeof rightValue === "number" ? leftValue - rightValue : String(leftValue).localeCompare(String(rightValue), "id-ID", { numeric: true, sensitivity: "base" });
      return loSort.direction === "asc" ? comparison : -comparison;
    });
  }, [loSearch, loSort, loadingOrders]);
  const loPageCount = Math.max(1, Math.ceil(filteredLO.length / loPerPage));
  const pagedLO = filteredLO.slice((loPage - 1) * loPerPage, loPage * loPerPage);
  const loRangeStart = filteredLO.length ? (loPage - 1) * loPerPage + 1 : 0;
  const loRangeEnd = Math.min(loPage * loPerPage, filteredLO.length);

  const filteredMT = useMemo(() => {
    const query = mtSearch.trim().toLocaleLowerCase("id-ID");
    const rows = query ? vehicles.filter((row) => [row.mt_id, row.registration, row.vehicle_class, row.tags.join(" "), row.capacity_kl, row.number_of_compartments, row.operational_status, vehicleDrafts[row.mt_id]?.status].some((value) => String(value ?? "").toLocaleLowerCase("id-ID").includes(query))) : vehicles;
    const value = (row: Vehicle): string | number => ({
      registration: row.registration || row.mt_id,
      class_tags: `${row.vehicle_class ?? ""} ${row.tags.join(" ")}`,
      capacity_kl: row.capacity_kl,
      compartments: row.number_of_compartments,
      planned_eta: vehicleDrafts[row.mt_id]?.planned || row.planned_eta_depot || "",
      system_eta: row.system_eta_depot || "",
      user_eta: vehicleDrafts[row.mt_id]?.override || row.user_eta_override || "",
      effective_eta: row.effective_eta_depot || "",
      status: vehicleDrafts[row.mt_id]?.status || row.operational_status,
      working_time: row.working_time_remaining,
    })[mtSort.key];
    return [...rows].sort((left, right) => {
      const leftValue = value(left); const rightValue = value(right);
      const comparison = typeof leftValue === "number" && typeof rightValue === "number" ? leftValue - rightValue : String(leftValue).localeCompare(String(rightValue), "id-ID", { numeric: true, sensitivity: "base" });
      return mtSort.direction === "asc" ? comparison : -comparison;
    });
  }, [mtSearch, mtSort, vehicleDrafts, vehicles]);
  const mtPageCount = Math.max(1, Math.ceil(filteredMT.length / mtPerPage));
  const pagedMT = filteredMT.slice((mtPage - 1) * mtPerPage, mtPage * mtPerPage);
  const mtRangeStart = filteredMT.length ? (mtPage - 1) * mtPerPage + 1 : 0;
  const mtRangeEnd = Math.min(mtPage * mtPerPage, filteredMT.length);

  useEffect(() => { setJobPage(1); }, [selectedDepot, jobSearch, jobsPerPage, jobSort.key, jobSort.direction]);
  useEffect(() => { if (jobPage > jobPageCount) setJobPage(jobPageCount); }, [jobPage, jobPageCount]);
  useEffect(() => { setLOPage(1); }, [loSearch, loPerPage, loSort.key, loSort.direction, job?.job_id]);
  useEffect(() => { if (loPage > loPageCount) setLOPage(loPageCount); }, [loPage, loPageCount]);
  useEffect(() => { setMTPage(1); }, [mtSearch, mtPerPage, mtSort.key, mtSort.direction, job?.job_id]);
  useEffect(() => { if (mtPage > mtPageCount) setMTPage(mtPageCount); }, [mtPage, mtPageCount]);
  useEffect(() => { setRouteMTPage(1); }, [selectedMT, selectedVersion, routeMTPerPage, job?.job_id]);
  useEffect(() => { if (routeMTPage > routeMTPageCount) setRouteMTPage(routeMTPageCount); }, [routeMTPage, routeMTPageCount]);
  useEffect(() => {
    if (selectedMT && routeVehicleOptions.length && !routeVehicleOptions.some(([vehicleId]) => vehicleId === selectedMT)) {
      setSelectedMT("");
      setSelectedTrip("ALL");
    }
  }, [routeVehicleOptions, selectedMT]);

  function toggleJobSort(key: JobSortKey) {
    setJobSort((current) => current.key === key
      ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key, direction: "asc" });
  }

  function jobSortButton(label: string, key: JobSortKey) {
    const active = jobSort.key === key;
    return <button type="button" className={active ? "phase7-sort-button is-active" : "phase7-sort-button"} onClick={() => toggleJobSort(key)} aria-label={`Sort ${label} ${active && jobSort.direction === "asc" ? "descending" : "ascending"}`}><span>{label}</span><ArrowUpDown size={13} /><i>{active ? (jobSort.direction === "asc" ? "ASC" : "DESC") : ""}</i></button>;
  }

  function loSortButton(label: string, key: LOSortKey) {
    const active = loSort.key === key;
    return <button type="button" className={active ? "phase7-sort-button is-active" : "phase7-sort-button"} onClick={() => setLOSort((current) => current.key === key ? { key, direction: current.direction === "asc" ? "desc" : "asc" } : { key, direction: "asc" })}><span>{label}</span><ArrowUpDown size={13} /><i>{active ? (loSort.direction === "asc" ? "ASC" : "DESC") : ""}</i></button>;
  }

  function mtSortButton(label: string, key: MTSortKey) {
    const active = mtSort.key === key;
    return <button type="button" className={active ? "phase7-sort-button is-active" : "phase7-sort-button"} onClick={() => setMTSort((current) => current.key === key ? { key, direction: current.direction === "asc" ? "desc" : "asc" } : { key, direction: "asc" })}><span>{label}</span><ArrowUpDown size={13} /><i>{active ? (mtSort.direction === "asc" ? "ASC" : "DESC") : ""}</i></button>;
  }

  async function createNewJob() {
    if (!selectedDepot || !createForm.job_name.trim()) return;
    await runAction(async () => {
      const created = await apiSend<JobDetail>("/api/v1/phase7/jobs", "POST", { depot_id: selectedDepot, ...createForm });
      setCreateOpen(false); await loadJobs(selectedDepot); await loadWorkspace(created.job_id); setTab("overview");
    }, "New Phase 7 Job created.");
  }

  async function deleteSelectedJob() {
    if (!deleteJobTarget) return;
    const target = deleteJobTarget;
    const completed = await runAction(async () => {
      await apiSend(`/api/v1/phase7/jobs/${encodeURIComponent(target.job_id)}`, "DELETE");
      await loadJobs(target.depot_id);
    }, `${target.job_no} deleted.`);
    if (completed) setDeleteJobTarget(null);
  }

  async function refreshWorkspace(versionId?: string) {
    if (!job) return;
    await loadWorkspace(job.job_id, versionId || selectedVersion || undefined);
  }

  async function loadPhase6() {
    if (!job || !selectedPredictionRun) return;
    await runAction(async () => {
      await apiSend(`/api/v1/phase7/jobs/${job.job_id}/prediction-run`, "POST", { run_id: selectedPredictionRun });
      await refreshWorkspace();
    }, "Phase 6 LO and warm start imported without changing the source run.");
  }

  async function applyLOUpdate() {
    if (!job || !selectedLO.size) return;
    await runAction(async () => {
      await apiSend(`/api/v1/phase7/jobs/${job.job_id}/loading-orders/status`, "PATCH", { updates: Array.from(selectedLO).map((loading_order_id) => ({ loading_order_id, status: bulkLOStatus })) });
      setSelectedLO(new Set()); await refreshWorkspace();
    }, "Operational LO state applied.");
  }

  async function loadMasterMT() {
    if (!job) return;
    await runAction(async () => { await apiSend(`/api/v1/phase7/jobs/${job.job_id}/vehicles/load-master`, "POST"); await refreshWorkspace(); }, "Depot MT loaded from canonical master data.");
  }

  async function applyVehicleUpdates() {
    if (!job) return;
    await runAction(async () => {
      await apiSend(`/api/v1/phase7/jobs/${job.job_id}/vehicles`, "PATCH", { updates: vehicles.map((vehicle) => ({ mt_id: vehicle.mt_id, planned_eta_depot: vehicleDrafts[vehicle.mt_id]?.planned ? new Date(vehicleDrafts[vehicle.mt_id].planned).toISOString() : null, user_eta_override: vehicleDrafts[vehicle.mt_id]?.override ? new Date(vehicleDrafts[vehicle.mt_id].override).toISOString() : null, operational_status: vehicleDrafts[vehicle.mt_id]?.status || vehicle.operational_status })) });
      await refreshWorkspace();
    }, "MT actual availability overrides applied.");
  }

  function openPlannedETADemoDialog() {
    if (!job) return;
    setPlannedETADemoDialog({
      date: job.operating_date,
      time: job.depot_operational_start?.slice(0, 5) || "00:00",
    });
  }

  async function applyPlannedETADemo() {
    if (!job || !plannedETADemoDialog || !vehicles.length) return;
    const localValue = `${plannedETADemoDialog.date}T${plannedETADemoDialog.time}`;
    const parsed = new Date(localValue);
    if (Number.isNaN(parsed.getTime())) {
      setError("Tanggal dan waktu Planned ETA Depot tidak valid.");
      return;
    }
    const plannedISO = parsed.toISOString();
    const completed = await runAction(async () => {
      await apiSend(`/api/v1/phase7/jobs/${job.job_id}/vehicles`, "PATCH", {
        updates: vehicles.map((vehicle) => ({
          mt_id: vehicle.mt_id,
          planned_eta_depot: plannedISO,
          user_eta_override: vehicleDrafts[vehicle.mt_id]?.override ? new Date(vehicleDrafts[vehicle.mt_id].override).toISOString() : vehicle.user_eta_override,
          operational_status: vehicleDrafts[vehicle.mt_id]?.status || vehicle.operational_status,
        })),
      });
      await refreshWorkspace();
    }, `Planned ETA Depot Demo diterapkan ke ${vehicles.length} MT.`);
    if (completed) setPlannedETADemoDialog(null);
  }

  async function createDefaultBaySet() {
    if (!job) return;
    const activeProducts = products.filter((row) => row.active_status !== "DELETED");
    await runAction(async () => {
      await apiSend(`/api/v1/phase7/depots/${job.depot_id}/bays`, "PUT", {
        bays: [
          { bay_id: "BAY-1", bay_name: "Bay 1 - All Products", all_products_allowed: true, allowed_products: [], operational_start: "05:00", operational_end: "22:00", number_of_loading_arms: 1, loading_mode: "SEQUENTIAL", active_status: "ACTIVE" },
          { bay_id: "BAY-2", bay_name: "Bay 2 - Configured Products", all_products_allowed: false, allowed_products: activeProducts.map((row) => row.product_id), operational_start: "05:00", operational_end: "22:00", number_of_loading_arms: 1, loading_mode: "SEQUENTIAL", active_status: "ACTIVE" },
        ],
        loading_durations: activeProducts.map((row) => ({ product_id: row.product_id, duration_minutes_per_compartment: 8 })),
      });
      await refreshWorkspace();
    }, "Editable default bay configuration created. Review product durations before optimization.");
  }

  async function saveBayConfiguration() {
    if (!job || !bay) return;
    await runAction(async () => {
      await apiSend(`/api/v1/phase7/depots/${job.depot_id}/bays`, "PUT", {
        bays: bay.configuration.bays.map((row) => ({
          bay_id: String(row.bay_id || ""),
          bay_name: String(row.bay_name || row.bay_id || ""),
          all_products_allowed: Boolean(row.all_products_allowed),
          allowed_products: (row.allowed_products as string[]) || [],
          operational_start: String(row.operational_start || "05:00"),
          operational_end: String(row.operational_end || "22:00"),
          number_of_loading_arms: Number(row.number_of_loading_arms || 1),
          loading_mode: String(row.loading_mode || "SEQUENTIAL"),
          active_status: String(row.active_status || "ACTIVE"),
        })),
        loading_durations: bay.configuration.loading_durations.map((row) => ({
          product_id: String(row.product_id),
          duration_minutes_per_compartment: Number(row.duration_minutes_per_compartment || 1),
        })),
      });
      await refreshWorkspace();
    }, "Bay configuration and loading durations saved.");
  }

  function updateBayConfiguration(index: number, field: string, value: unknown) {
    setBay((current) => current ? { ...current, configuration: { ...current.configuration, bays: current.configuration.bays.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row) } } : current);
  }

  function updateLoadingDuration(index: number, value: number) {
    setBay((current) => current ? { ...current, configuration: { ...current.configuration, loading_durations: current.configuration.loading_durations.map((row, rowIndex) => rowIndex === index ? { ...row, duration_minutes_per_compartment: value } : row) } } : current);
  }

  function addBayConfiguration() {
    if (!bay) return;
    const suffix = bay.configuration.bays.length + 1;
    setBay({ ...bay, configuration: { ...bay.configuration, bays: [...bay.configuration.bays, { bay_id: `BAY-${suffix}`, bay_name: `Bay ${suffix}`, all_products_allowed: true, allowed_products: [], operational_start: "05:00", operational_end: "22:00", number_of_loading_arms: 1, loading_mode: "SEQUENTIAL", active_status: "ACTIVE" }] } });
  }

  function requestDeleteBay(row: Record<string, unknown>, index: number) {
    if (!row.master_bay_id) {
      setBay((current) => current ? { ...current, configuration: { ...current.configuration, bays: current.configuration.bays.filter((_, rowIndex) => rowIndex !== index) } } : current);
      return;
    }
    setDeleteBayTarget(row);
  }

  async function deleteSelectedBay() {
    if (!job || !deleteBayTarget?.master_bay_id) return;
    const bayName = String(deleteBayTarget.bay_name || deleteBayTarget.bay_id || "Bay");
    const completed = await runAction(async () => {
      await apiSend(`/api/v1/phase7/depots/${encodeURIComponent(job.depot_id)}/bays/${encodeURIComponent(String(deleteBayTarget.master_bay_id))}`, "DELETE");
      await refreshWorkspace();
    }, `${bayName} deleted from the active depot bay configuration.`);
    if (completed) setDeleteBayTarget(null);
  }

  async function saveActualBayState() {
    if (!job || !bay) return;
    await runAction(async () => {
      const states = bay.configuration.bays.map((row) => {
        const existing = bay.states.find((state) => state.master_bay_id === row.master_bay_id);
        return { master_bay_id: row.master_bay_id, current_vehicle_id: existing?.current_vehicle_id || null, current_compartment_id: existing?.current_compartment_id || null, current_product_id: existing?.current_product_id || null, remaining_loading_minutes: Number(existing?.remaining_loading_minutes || 0), actual_queue_length: Number(existing?.actual_queue_length || 0) };
      });
      const queue = bay.queue.map(({ state_effective_at: _stateEffectiveAt, ...row }) => row);
      await apiSend(`/api/v1/phase7/jobs/${job.job_id}/bay-state`, "PUT", { states, queue });
      await refreshWorkspace();
    }, "Actual bay occupancy and queue applied.");
  }

  function updateBayState(masterBayId: unknown, field: string, value: unknown) {
    setBay((current) => {
      if (!current) return current;
      const existing = current.states.find((state) => state.master_bay_id === masterBayId) || {};
      return {
        ...current,
        states: [
          ...current.states.filter((state) => state.master_bay_id !== masterBayId),
          { ...existing, master_bay_id: masterBayId, [field]: value },
        ],
      };
    });
  }

  function addBayQueueRow() {
    if (!bay?.configuration.bays.length || !vehicles.length || !products.length) return;
    const masterBayId = bay.configuration.bays[0].master_bay_id;
    const nextPosition = Math.max(0, ...bay.queue.filter((row) => row.master_bay_id === masterBayId).map((row) => Number(row.queue_position || 0))) + 1;
    setBay({
      ...bay,
      queue: [
        ...bay.queue,
        {
          master_bay_id: masterBayId,
          queue_position: nextPosition,
          vehicle_id: vehicles[0].mt_id,
          compartment_id: vehicles[0].compartments[0]?.compartment_id || null,
          product_id: products[0].product_id,
          estimated_loading_duration_minutes: 8,
        },
      ],
    });
  }

  function updateBayQueueRow(index: number, field: string, value: unknown) {
    setBay((current) => current ? { ...current, queue: current.queue.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row) } : current);
  }

  async function validateWorkspace() {
    if (!job) return;
    setReadinessOpen(false);
    await runAction(async () => {
      const result = await apiSend<NonNullable<typeof validation>>(`/api/v1/phase7/jobs/${job.job_id}/validation`, "POST", { parameters: parameterDraft });
      setValidation(result);
      setReadinessOpen(true);
      await refreshWorkspace();
    }, "Pre-optimization validation completed against the current constraint settings.");
  }

  function openOptimizationDialog(reroute: boolean) {
    if (!job) return;
    const initialParts = job.initial_optimization_reference_time
      ? zonedDateTimeParts(job.initial_optimization_reference_time, job.depot_timezone)
      : null;
    const latestParts = job.latest_optimization_reference_time
      ? zonedDateTimeParts(job.latest_optimization_reference_time, job.depot_timezone)
      : null;
    const date = reroute ? initialParts?.date || job.operating_date : job.operating_date;
    const nowParts = zonedDateTimeParts(new Date(), job.depot_timezone);
    let time = nowParts.date === date ? nowParts.time : job.depot_operational_start.slice(0, 5);
    if (reroute && latestParts?.date === date && latestParts.time > time) time = latestParts.time;
    setOptimizationDialog({ reroute, date, time });
    setError("");
    setNotice("");
  }

  async function optimize(reroute: boolean, referenceTime: string) {
    if (!job) return;
    const targetDepot = job.depot_id;
    await runAction(async () => {
      await apiSend<OptimizationDispatch>(`/api/v1/phase7/jobs/${job.job_id}/${reroute ? "reroute" : "optimize"}`, "POST", { profile_id: selectedProfile || null, parameters: parameterDraft, current_time: referenceTime, reason: reroute ? "Operational Reroute" : "Initial Plan" });
      setOptimizationDialog(null);
      setJob(null);
      setRouteVersion(null);
      setSelectedVersion("");
      setValidation(null);
      setReadinessOpen(false);
      setTab("overview");
      await loadJobs(targetDepot);
    }, reroute ? "Re-optimization diterima. Worker berjalan di background; status Job diperbarui otomatis." : "Initial optimization diterima. Worker berjalan di background; status Job diperbarui otomatis.");
  }

  async function saveProfile(saveAs: boolean) {
    const name = saveAs ? `${selectedProfileRow?.profile_name || "Custom Profile"} Copy` : selectedProfileRow?.profile_name || "Custom Profile";
    await runAction(async () => {
      const result = await apiSend<ParameterProfile>(saveAs ? "/api/v1/phase7/parameter-profiles" : `/api/v1/phase7/parameter-profiles/${selectedProfile}`, saveAs ? "POST" : "PUT", { profile_name: name, description: selectedProfileRow?.description, parameters: parameterDraft });
      setProfiles(await apiGet<ParameterProfile[]>("/api/v1/phase7/parameter-profiles")); setSelectedProfile(result.profile_id);
    }, `Parameter profile ${saveAs ? "saved as a new profile" : "saved as a new version"}.`);
  }

  async function selectRouteVersion(value: string) {
    if (!job || !value) return;
    setSelectedVersion(value);
    setRouteVersion(await apiGet<RouteVersion>(`/api/v1/phase7/jobs/${job.job_id}/versions/${value}`));
  }

  if (!job) {
    return (
      <div className="phase7-shell">
        <div className="phase7-toolbar">
          <div><span className="phase7-overline">Phase 7 · Job Management</span><h2>Dynamic Multi-Trip Optimization Jobs</h2><p>Select one depot first. Jobs remain depot-scoped and never pull Loading Orders by date alone.</p></div>
        </div>
        <Section title="Job Management" description="Create, find, sort, open, or delete a depot-scoped operational workspace." action={<button className="phase7-primary" disabled={!selectedDepot} onClick={() => setCreateOpen(true)}><Plus size={16} /> Create New Job</button>}>
          <div className="phase7-filter-row phase7-job-filters">
            <label><span>Depot</span><select value={selectedDepot} onChange={(event) => setSelectedDepot(event.target.value)}><option value="">Select Depot</option>{depots.map((depot) => <option key={depot.depot_id} value={depot.depot_id}>{depot.depot_name}</option>)}</select></label>
            <label><span>Search Job</span><div className="phase7-search-box"><Search size={15} /><input value={jobSearch} placeholder="Job ID, name, date, depot, route, or status" onChange={(event) => setJobSearch(event.target.value)} /></div></label>
            <button className="phase7-secondary" disabled={!selectedDepot} onClick={() => void loadJobs(selectedDepot)}><RefreshCw size={15} /> Refresh</button>
          </div>
          {!selectedDepot ? <EmptyState title="Select Depot" description="The job list appears only after a depot is selected." /> : jobs.length === 0 ? <EmptyState title="No Phase 7 Job" description="Create a job for this depot and operating date." /> : filteredJobs.length === 0 ? <EmptyState title="No matching Job" description="Change or clear the search text to show another Job." /> : <>
            <div className="phase7-table-wrap"><table className="phase7-table phase7-job-table"><thead><tr><th>{jobSortButton("Job ID", "job_no")}</th><th>{jobSortButton("Operating Date", "operating_date")}</th><th>{jobSortButton("Depot", "depot")}</th><th>{jobSortButton("Total LO", "total_lo")}</th><th>{jobSortButton("Total MT", "total_mt")}</th><th>{jobSortButton("Current Route", "current_route_version")}</th><th>{jobSortButton("Status", "status")}</th><th>{jobSortButton("Last Updated", "last_updated")}</th><th>Action</th></tr></thead><tbody>{pagedJobs.map((row) => <tr key={row.job_id}><td><strong>{row.job_no}</strong><small>{row.job_name}</small></td><td>{row.operating_date}</td><td>{row.depot}</td><td>{row.total_lo}</td><td>{row.total_mt}</td><td>{row.current_route_version || "—"}</td><td><span className={badgeClass(row.status)}>{row.status === "CALCULATING" && <LoaderCircle className="animate-spin" size={13} />} {row.status}</span>{row.status === "CALCULATING" && <small>Background worker</small>}</td><td>{displayDateTime(row.last_updated)}</td><td><div className="phase7-job-actions"><button className="phase7-link" onClick={() => void openJob(row.job_id)}>Open Job <ChevronRight size={14} /></button><button className="phase7-delete-button" disabled={busy || row.status === "CALCULATING"} title={row.status === "CALCULATING" ? "Job cannot be deleted while optimization is calculating" : `Delete ${row.job_no}`} onClick={() => setDeleteJobTarget(row)}><Trash2 size={14} /> Delete</button></div></td></tr>)}</tbody></table></div>
            <div className="phase7-pagination"><span>Showing {jobRangeStart}–{jobRangeEnd} of {filteredJobs.length} Jobs</span><label><span>Rows</span><select value={jobsPerPage} onChange={(event) => setJobsPerPage(Number(event.target.value))}><option value={5}>5</option><option value={10}>10</option><option value={25}>25</option><option value={50}>50</option></select></label><div><button className="phase7-secondary" disabled={jobPage <= 1} onClick={() => setJobPage((page) => Math.max(1, page - 1))}><ChevronLeft size={15} /> Previous</button><strong>Page {jobPage} of {jobPageCount}</strong><button className="phase7-secondary" disabled={jobPage >= jobPageCount} onClick={() => setJobPage((page) => Math.min(jobPageCount, page + 1))}>Next <ChevronRight size={15} /></button></div></div>
          </>}
        </Section>
        {createOpen && <div className="phase7-modal-backdrop"><div className="phase7-modal"><button className="phase7-modal-close" onClick={() => setCreateOpen(false)}><X size={18} /></button><span className="phase7-overline">Create New Job</span><h3>New operational workspace</h3><label><span>Depot</span><select value={selectedDepot} disabled><option value={selectedDepot}>{depots.find((row) => row.depot_id === selectedDepot)?.depot_name}</option></select></label><label><span>Operating Date</span><input type="date" value={createForm.operating_date} onChange={(event) => setCreateForm((current) => ({ ...current, operating_date: event.target.value }))} /></label><label><span>Job Name</span><input value={createForm.job_name} maxLength={255} placeholder="Morning dispatch control" onChange={(event) => setCreateForm((current) => ({ ...current, job_name: event.target.value }))} /></label><button className="phase7-primary" disabled={busy || !createForm.job_name.trim()} onClick={() => void createNewJob()}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : <Plus size={16} />} Create & Open Workspace</button></div></div>}
        {deleteJobTarget && <div className="phase7-modal-backdrop"><div className="phase7-modal" role="alertdialog" aria-modal="true" aria-labelledby="phase7-delete-job-title"><button type="button" className="phase7-modal-close" disabled={busy} onClick={() => setDeleteJobTarget(null)} aria-label="Close delete Job dialog"><X size={18} /></button><span className="phase7-overline">Delete Phase 7 Job</span><h3 id="phase7-delete-job-title">Hapus {deleteJobTarget.job_no}?</h3><div className="phase7-delete-warning"><AlertTriangle size={20} /><div><strong>Tindakan ini tidak dapat dibatalkan.</strong><span>Workspace, LO/MT operational state, route version, snapshot, dan hasil optimasi Job akan dihapus. Source Prediction Run Phase 6 dan master data tidak ikut dihapus.</span></div></div><div className="phase7-reference-summary"><span><small>Job Name</small><strong>{deleteJobTarget.job_name}</strong></span><span><small>Status</small><strong>{deleteJobTarget.status}</strong></span><span><small>Operating Date</small><strong>{deleteJobTarget.operating_date}</strong></span><span><small>Current Route</small><strong>{deleteJobTarget.current_route_version || "Belum ada"}</strong></span></div><div className="phase7-action-row is-modal-actions"><button className="phase7-secondary" disabled={busy} onClick={() => setDeleteJobTarget(null)}>Batal</button><button className="phase7-delete-confirm" disabled={busy} onClick={() => void deleteSelectedJob()}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : <Trash2 size={16} />} Delete Job</button></div></div></div>}
        {(error || notice) && <div className={`phase7-toast ${error ? "is-error" : "is-success"}`}>{error || notice}</div>}
      </div>
    );
  }

  const headerKpis = ["total_lo", "done_lo", "ongoing_lo", "planned_lo", "dropped_lo", "used_mt", "total_trips", "delivered_kl", "remaining_kl"].map((key) => ({ label: key.replace(/_/g, " "), value: job.kpis[key] ?? 0 }));
  const profileNumber = (key: string, fallback = 0) => Number(parameterDraft[key] ?? fallback);
  const constraintRules = (parameterDraft.constraint_rules || {}) as Record<string, ConstraintRule>;
  const constraintRuleFor = (definition: ConstraintDefinition): ConstraintRule => constraintRules[definition.constraint_id] || {
    enabled: true,
    mode: definition.default_mode,
    penalty: definition.default_penalty,
    ...(definition.default_limit_minutes ? { limit_minutes: definition.default_limit_minutes } : {}),
  };
  const updateConstraintRule = (definition: ConstraintDefinition, changes: Partial<ConstraintRule>) => {
    setParameterDraft((current) => {
      const currentRules = (current.constraint_rules || {}) as Record<string, ConstraintRule>;
      const existing = currentRules[definition.constraint_id] || constraintRuleFor(definition);
      return { ...current, constraint_rules: { ...currentRules, [definition.constraint_id]: { ...existing, ...changes } } };
    });
    setValidation(null);
    setReadinessOpen(false);
  };
  const initialReferenceParts = job.initial_optimization_reference_time ? zonedDateTimeParts(job.initial_optimization_reference_time, job.depot_timezone) : null;
  const latestReferenceParts = job.latest_optimization_reference_time ? zonedDateTimeParts(job.latest_optimization_reference_time, job.depot_timezone) : null;
  let optimizationDialogError = "";
  if (optimizationDialog) {
    const lockedDate = optimizationDialog.reroute ? initialReferenceParts?.date || job.operating_date : job.operating_date;
    if (!optimizationDialog.date || !optimizationDialog.time) optimizationDialogError = "Tanggal dan waktu optimasi wajib diisi.";
    else if (optimizationDialog.date !== lockedDate) optimizationDialogError = optimizationDialog.reroute
      ? `Tanggal Re-optimize dikunci ke tanggal Initial ${lockedDate}.`
      : `Tanggal Initial harus sama dengan Operating Date Job ${job.operating_date}.`;
    else if (optimizationDialog.reroute && latestReferenceParts?.date === optimizationDialog.date && optimizationDialog.time < latestReferenceParts.time) optimizationDialogError = `Waktu Re-optimize tidak boleh lebih awal dari run terakhir (${latestReferenceParts.time}).`;
  }

  return (
    <div className="phase7-shell">
      <div className="phase7-job-header">
        <div className="phase7-job-topline"><button className="phase7-link" onClick={() => { setJob(null); setRouteVersion(null); void loadJobs(selectedDepot); }}>← Job Management</button><span className={badgeClass(job.status)}>{job.status}</span></div>
        <div className="phase7-job-identity"><div><span>Job ID</span><strong>{job.job_no}</strong></div><div><span>Depot</span><strong>{job.depot}</strong></div><div><span>Operating Date</span><strong>{job.operating_date}</strong></div><div><span>Source Phase 6 Run</span><strong>{job.header.source_phase6_run_id || "Not loaded"}</strong></div><div><span>Current Route Version</span><strong>{job.current_route_version || "—"}</strong></div><div><span>Latest Optimization Time</span><strong>{job.latest_optimization_reference_time ? `${displayDateTime(job.latest_optimization_reference_time, job.depot_timezone)} · ${job.depot_timezone}` : "—"}</strong></div></div>
        <KpiGrid values={headerKpis} />
      </div>
      <nav className="phase7-tabs" aria-label="Phase 7 job workspace pages">{tabs.map((item) => { const Icon = item.icon; return <button key={item.id} className={tab === item.id ? "is-active" : ""} onClick={() => setTab(item.id)}><Icon size={15} />{item.label}</button>; })}</nav>
      {(error || notice) && <div className={`phase7-inline-alert ${error ? "is-error" : "is-success"}`}>{error ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}{error || notice}</div>}

      {tab === "overview" && <div className="phase7-grid-2">
        <Section title="Prediction Run History" description="Required first step. Phase 6 remains an immutable warm start and soft preference.">
          <label className="phase7-field"><span>Run ID</span><select value={selectedPredictionRun} disabled={Boolean(job.source_prediction_run_id)} onChange={(event) => setSelectedPredictionRun(event.target.value)}><option value="">Select completed Phase 6 Run ID</option>{predictionRuns.map((run) => <option key={run.id} value={run.id}>{run.run_id}</option>)}</select></label>
          {selectedRun && <div className="phase7-meta-grid"><span><small>Run ID</small>{selectedRun.run_id}</span><span><small>Date / Depot</small>{selectedRun.date} · {selectedRun.depot}</span><span><small>Total LO</small>{selectedRun.total_lo}</span><span><small>Predicted Shipment</small>{selectedRun.predicted_shipment_count}</span><span><small>Predicted MT</small>{selectedRun.predicted_mt_count}</span><span><small>Model</small>{selectedRun.model_name} / {selectedRun.model_id}</span><span><small>Saved At</small>{displayDateTime(selectedRun.saved_at)}</span></div>}
          <button className="phase7-primary" disabled={!selectedPredictionRun || Boolean(job.source_prediction_run_id) || busy} onClick={() => void loadPhase6()}><Database size={16} /> Load Prediction Run</button>
          {job.source_prediction_run_id && <div className="phase7-note"><CheckCircle2 size={16} /> Source locked. Reroutes use current operational state and never rerun Phase 6 automatically.</div>}
        </Section>
        <Section title="Optimization Flow" description="Every run asks for an explicit operational reference time and creates an immutable route version, state snapshot, and parameter snapshot.">
          <div className="phase7-step-list">{["Load Phase 6 Prediction Run", "Load MT from Master", "Enter initial MT ETA Depot", "Enter current Bay State", "Select and review Parameter Profile", "Validate hard requirements", "Set optimization date & time", "Run Initial Optimization → V1"].map((label, index) => <div key={label}><span>{index + 1}</span><strong>{label}</strong></div>)}</div>
          <div className="phase7-action-row"><button className="phase7-secondary" disabled={busy || job.status === "CALCULATING"} onClick={() => void validateWorkspace()}><CheckCircle2 size={16} /> Validate</button><button className="phase7-primary" disabled={busy || job.status === "CALCULATING" || !job.source_prediction_run_id || Boolean(job.current_route_version_id)} onClick={() => openOptimizationDialog(false)}><Play size={16} /> Run Initial Optimization</button><button className="phase7-secondary" disabled={busy || job.status === "CALCULATING" || !job.current_route_version_id} onClick={() => openOptimizationDialog(true)}><RefreshCw size={16} /> Re-Optimize Now</button></div>
          {job.initial_optimization_reference_time && <div className="phase7-note"><CalendarClock size={16} /> Initial reference: {displayDateTime(job.initial_optimization_reference_time, job.depot_timezone)} ({job.depot_timezone}). Re-optimize date remains locked to {initialReferenceParts?.date}.</div>}
        </Section>
        <Section title="Current Plan Stability" description="V1 is baseline; the latest route version is the current operational plan.">
          {routeVersion ? <KpiGrid values={[{ label: "Plan Adherence", value: `${routeVersion.comparison.plan_adherence_pct ?? 100}%` }, { label: "Vehicle Changes", value: Number(routeVersion.comparison.vehicle_assignment_changes || 0) }, { label: "Shipment Changes", value: Number(routeVersion.comparison.shipment_changes || 0) }, { label: "Gate-Out Variance", value: `${routeVersion.comparison.gate_out_variance_minutes || 0} min` }]} /> : <EmptyState title="No baseline version" description="The first successful optimization creates V1." />}
        </Section>
      </div>}

      {tab === "lo" && <Section title="LO Management" description="Phase 6 fields remain read-only; Phase 7 current assignment and operational status are separate." action={<div className="phase7-action-row"><select value={bulkLOStatus} onChange={(event) => setBulkLOStatus(event.target.value as LoadingOrder["status"])}><option>PLANNED</option><option>ONGOING</option><option>DONE</option></select><button className="phase7-primary" disabled={!selectedLO.size || busy} onClick={() => void applyLOUpdate()}>Apply Operational Update ({selectedLO.size})</button></div>}>
        {!!loadingOrders.length && <div className="phase7-management-search"><Search size={16} /><input value={loSearch} placeholder="Search LO ID, SPBU, product, shipment, MT, status, or frozen reason" onChange={(event) => setLOSearch(event.target.value)} /></div>}
        {!loadingOrders.length ? <EmptyState title="No LO loaded" description="Open Job Overview and load a Phase 6 Prediction Run." /> : !filteredLO.length ? <EmptyState title="No matching LO" description="Change or clear the search text." /> : <><div className="phase7-table-wrap"><table className="phase7-table is-dense phase7-operational-table"><thead><tr><th><input type="checkbox" aria-label="Select all LO on this page" checked={pagedLO.length > 0 && pagedLO.every((row) => selectedLO.has(row.loading_order_id))} onChange={(event) => setSelectedLO((current) => { const next = new Set(current); pagedLO.forEach((row) => event.target.checked ? next.add(row.loading_order_id) : next.delete(row.loading_order_id)); return next; })} /></th><th>{loSortButton("LO ID", "loading_order_id")}</th><th>{loSortButton("SPBU", "spbu")}</th><th>{loSortButton("Product", "product")}</th><th>{loSortButton("Volume", "volume_kl")}</th><th>{loSortButton("Phase 6 Shipment", "phase6_shipment")}</th><th>{loSortButton("Phase 6 MT", "phase6_mt")}</th><th>{loSortButton("Current MT", "current_mt")}</th><th>{loSortButton("Trip / Compartment", "trip")}</th><th>{loSortButton("Planned Gate Out", "planned_gate_out")}</th><th>{loSortButton("Status", "status")}</th><th>{loSortButton("Frozen", "frozen")}</th></tr></thead><tbody>{pagedLO.map((row) => <tr key={row.loading_order_id}><td><input type="checkbox" checked={selectedLO.has(row.loading_order_id)} onChange={(event) => setSelectedLO((current) => { const next = new Set(current); event.target.checked ? next.add(row.loading_order_id) : next.delete(row.loading_order_id); return next; })} /></td><td><strong>{row.loading_order_id}</strong></td><td>{row.spbu_name || row.spbu_id}<small>{row.spbu_id}</small></td><td>{row.product_name || row.product_id || "—"}</td><td>{row.volume_kl} KL</td><td>{row.phase6_shipment || "—"}</td><td>{row.phase6_mt || "—"}</td><td>{row.current_mt || "—"}</td><td>{row.current_trip ? `Trip ${row.current_trip}` : "—"}<small>{row.current_compartment || ""}</small></td><td>{displayDateTime(row.planned_gate_out)}</td><td><span className={badgeClass(row.status)}>{row.status}</span></td><td>{row.frozen ? <span className="phase7-badge is-warning">{row.frozen_reason}</span> : "No"}</td></tr>)}</tbody></table></div><div className="phase7-pagination"><span>Showing {loRangeStart}–{loRangeEnd} of {filteredLO.length} LO</span><label><span>Rows</span><select value={loPerPage} onChange={(event) => setLOPerPage(Number(event.target.value))}><option value={10}>10</option><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><div><button className="phase7-secondary" disabled={loPage <= 1} onClick={() => setLOPage((page) => Math.max(1, page - 1))}><ChevronLeft size={15} /> Previous</button><strong>Page {loPage} of {loPageCount}</strong><button className="phase7-secondary" disabled={loPage >= loPageCount} onClick={() => setLOPage((page) => Math.min(loPageCount, page + 1))}>Next <ChevronRight size={15} /></button></div></div></>}
      </Section>}

      {tab === "mt" && <Section title="MT Management" description="Effective ETA = user override, otherwise previous system ETA, otherwise initial planned ETA." action={<div className="phase7-action-row"><button className="phase7-secondary" onClick={() => void loadMasterMT()}><Database size={15} /> Load MT from Master Data</button><button className="phase7-secondary" disabled={!vehicles.length || busy} onClick={openPlannedETADemoDialog}><CalendarClock size={15} /> Planned ETA Depot Demo</button><button className="phase7-primary" disabled={!vehicles.length || busy} onClick={() => void applyVehicleUpdates()}><Save size={15} /> Apply MT Update</button></div>}>
        {!!vehicles.length && <div className="phase7-management-search"><Search size={16} /><input value={mtSearch} placeholder="Search MT, registration, class, tag, capacity, or status" onChange={(event) => setMTSearch(event.target.value)} /></div>}
        {!vehicles.length ? <EmptyState title="No MT loaded" description="Load active vehicles for this Job depot from canonical master data." /> : !filteredMT.length ? <EmptyState title="No matching MT" description="Change or clear the search text." /> : <><div className="phase7-table-wrap"><table className="phase7-table is-dense phase7-operational-table"><thead><tr><th>{mtSortButton("MT / Registration", "registration")}</th><th>{mtSortButton("Class / Tags", "class_tags")}</th><th>{mtSortButton("Capacity", "capacity_kl")}</th><th>{mtSortButton("Compartments", "compartments")}</th><th>{mtSortButton("Planned ETA Depot", "planned_eta")}</th><th>{mtSortButton("System ETA Depot", "system_eta")}</th><th>{mtSortButton("User ETA Override", "user_eta")}</th><th>{mtSortButton("Effective ETA", "effective_eta")}</th><th>{mtSortButton("Status", "status")}</th><th>{mtSortButton("Working Time", "working_time")}</th></tr></thead><tbody>{pagedMT.map((row) => <tr key={row.mt_id}><td><strong>{row.registration || row.mt_id}</strong><small>{row.mt_id}</small></td><td>Class {row.vehicle_class ?? "—"}<small>{row.tags.join(", ") || "No tags"}</small></td><td>{row.capacity_kl} KL</td><td>{row.number_of_compartments}<small>{row.compartments.map((item) => `${item.compartment_id} ${item.capacity_kl} KL`).join(" · ")}</small></td><td><input type="datetime-local" value={vehicleDrafts[row.mt_id]?.planned || ""} onChange={(event) => setVehicleDrafts((current) => ({ ...current, [row.mt_id]: { ...(current[row.mt_id] || { override: "", status: row.operational_status }), planned: event.target.value } }))} /></td><td>{displayDateTime(row.system_eta_depot)}</td><td><input type="datetime-local" value={vehicleDrafts[row.mt_id]?.override || ""} onChange={(event) => setVehicleDrafts((current) => ({ ...current, [row.mt_id]: { ...(current[row.mt_id] || { planned: "", status: row.operational_status }), override: event.target.value } }))} /></td><td><strong>{displayDateTime(row.effective_eta_depot)}</strong></td><td><select value={vehicleDrafts[row.mt_id]?.status || row.operational_status} onChange={(event) => setVehicleDrafts((current) => ({ ...current, [row.mt_id]: { ...(current[row.mt_id] || { planned: "", override: "" }), status: event.target.value } }))}>{["READY", "ON_TRIP", "RETURNING", "QUEUEING", "LOADING", "UNAVAILABLE"].map((status) => <option key={status}>{status}</option>)}</select></td><td>{row.working_time_used} / {row.working_time_limit} min<small>{row.working_time_remaining} min remaining</small></td></tr>)}</tbody></table></div><div className="phase7-pagination"><span>Showing {mtRangeStart}–{mtRangeEnd} of {filteredMT.length} MT</span><label><span>Rows</span><select value={mtPerPage} onChange={(event) => setMTPerPage(Number(event.target.value))}><option value={10}>10</option><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><div><button className="phase7-secondary" disabled={mtPage <= 1} onClick={() => setMTPage((page) => Math.max(1, page - 1))}><ChevronLeft size={15} /> Previous</button><strong>Page {mtPage} of {mtPageCount}</strong><button className="phase7-secondary" disabled={mtPage >= mtPageCount} onClick={() => setMTPage((page) => Math.min(mtPageCount, page + 1))}>Next <ChevronRight size={15} /></button></div></div></>}
      </Section>}

      {tab === "bay" && <div className="phase7-grid-2">
        <div className="phase7-grid-span phase7-bay-section"><Section title="Bay Configuration" description="Product compatibility is hard. Loading duration is per product per compartment." action={<div className="phase7-action-row phase7-bay-config-actions"><button className="phase7-secondary" onClick={() => void createDefaultBaySet()}><RefreshCw size={15} /> Default Set</button><button className="phase7-secondary" disabled={!bay} onClick={addBayConfiguration}><Plus size={15} /> Add Bay</button><button className="phase7-primary" disabled={!bay?.configuration.bays.length} onClick={() => void saveBayConfiguration()}><Save size={15} /> Save Configuration</button></div>}>
          {!bay?.configuration.bays.length ? <EmptyState title="No bay configured" description="Create an editable initial set, then review products, hours, arms, and loading mode." /> : <div className="phase7-bay-grid">{bay.configuration.bays.map((row, index) => <div className="phase7-bay-state" key={String(row.master_bay_id || `${row.bay_id}-${index}`)}><div className="phase7-bay-editor-head"><strong>{String(row.bay_name || row.bay_id || `Bay ${index + 1}`)}</strong><button type="button" className="phase7-delete-button" disabled={busy} onClick={() => requestDeleteBay(row, index)}><Trash2 size={14} /> Delete Bay</button></div><label><span>Bay ID</span><input value={String(row.bay_id || "")} onChange={(event) => updateBayConfiguration(index, "bay_id", event.target.value)} /></label><label><span>Bay Name</span><input value={String(row.bay_name || "")} onChange={(event) => updateBayConfiguration(index, "bay_name", event.target.value)} /></label><label><span>Products</span><select multiple disabled={Boolean(row.all_products_allowed)} value={(row.allowed_products as string[]) || []} onChange={(event) => updateBayConfiguration(index, "allowed_products", Array.from(event.target.selectedOptions, (option) => option.value))}>{products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name}</option>)}</select></label><label className="phase7-check"><input type="checkbox" checked={Boolean(row.all_products_allowed)} onChange={(event) => updateBayConfiguration(index, "all_products_allowed", event.target.checked)} /><span>Allow all products</span></label><label><span>Operational Start</span><input type="time" value={String(row.operational_start || "05:00").slice(0, 5)} onChange={(event) => updateBayConfiguration(index, "operational_start", event.target.value)} /></label><label><span>Operational End</span><input type="time" value={String(row.operational_end || "22:00").slice(0, 5)} onChange={(event) => updateBayConfiguration(index, "operational_end", event.target.value)} /></label><label><span>Loading Arms</span><input type="number" min="1" value={Number(row.number_of_loading_arms || 1)} onChange={(event) => updateBayConfiguration(index, "number_of_loading_arms", Number(event.target.value))} /></label><label><span>Loading Mode</span><select value={String(row.loading_mode || "SEQUENTIAL")} onChange={(event) => updateBayConfiguration(index, "loading_mode", event.target.value)}><option>SEQUENTIAL</option><option>PARALLEL</option></select></label><label><span>Status</span><select value={String(row.active_status || "ACTIVE")} onChange={(event) => updateBayConfiguration(index, "active_status", event.target.value)}><option>ACTIVE</option><option>INACTIVE</option></select></label></div>)}</div>}
          {!!bay?.configuration.loading_durations.length && <div className="phase7-duration-section"><div className="phase7-duration-heading"><strong>Kecepatan Pengisian Product</strong><span>Durasi dalam menit untuk mengisi satu compartment.</span></div><div className="phase7-duration-list">{bay.configuration.loading_durations.map((row, index) => <label key={String(row.product_id)}><strong title={String(row.product_name || row.product_id)}>{String(row.product_name || row.product_id)}</strong><div><input type="number" min="1" value={Number(row.duration_minutes_per_compartment || 1)} onChange={(event) => updateLoadingDuration(index, Number(event.target.value))} /><span>min / compartment</span></div></label>)}</div></div>}
        </Section></div>
        <div className="phase7-grid-span phase7-bay-section"><Section title="Current Actual Bay State" description="Actual occupancy and queue override the previously predicted state on reroute." action={<button className="phase7-primary" disabled={!bay?.configuration.number_of_bays} onClick={() => void saveActualBayState()}><Save size={15} /> Apply Bay State</button>}>
          {!bay?.configuration.number_of_bays ? <EmptyState title="Configuration required" description="Create bay master configuration first." /> : <div className="phase7-bay-grid">{bay.configuration.bays.map((row) => { const existing = bay.states.find((state) => state.master_bay_id === row.master_bay_id); return <div className="phase7-bay-state" key={String(row.master_bay_id)}><strong>{String(row.bay_name)}</strong><label><span>Current Vehicle</span><select value={String(existing?.current_vehicle_id || "")} onChange={(event) => updateBayState(row.master_bay_id, "current_vehicle_id", event.target.value || null)}><option value="">Bay empty</option>{vehicles.map((vehicle) => <option key={vehicle.mt_id} value={vehicle.mt_id}>{vehicle.registration || vehicle.mt_id}</option>)}</select></label><label><span>Current Compartment</span><input value={String(existing?.current_compartment_id || "")} placeholder="C1" onChange={(event) => updateBayState(row.master_bay_id, "current_compartment_id", event.target.value || null)} /></label><label><span>Current Product</span><select value={String(existing?.current_product_id || "")} onChange={(event) => updateBayState(row.master_bay_id, "current_product_id", event.target.value || null)}><option value="">None</option>{products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name}</option>)}</select></label><label><span>Remaining Loading (min)</span><input type="number" min="0" value={Number(existing?.remaining_loading_minutes || 0)} onChange={(event) => updateBayState(row.master_bay_id, "remaining_loading_minutes", Number(event.target.value))} /></label><label><span>Actual Queue Length</span><input type="number" min="0" value={Number(existing?.actual_queue_length || 0)} onChange={(event) => updateBayState(row.master_bay_id, "actual_queue_length", Number(event.target.value))} /></label></div>; })}</div>}
          <div className="phase7-queue-head"><div><strong>Current Physical Queue</strong><span>Rows are reserved in queue order before CP-SAT schedules new loading.</span></div><button className="phase7-secondary" disabled={!bay?.configuration.number_of_bays || !vehicles.length || !products.length} onClick={addBayQueueRow}><Plus size={15} /> Add Queue Row</button></div>
          {!!bay?.queue.length && <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>Bay</th><th>Position</th><th>MT</th><th>Compartment</th><th>Product</th><th>Duration</th><th /></tr></thead><tbody>{bay.queue.map((queueRow, index) => <tr key={`${String(queueRow.master_bay_id)}-${index}`}><td><select value={String(queueRow.master_bay_id || "")} onChange={(event) => updateBayQueueRow(index, "master_bay_id", event.target.value)}>{bay.configuration.bays.map((bayRow) => <option key={String(bayRow.master_bay_id)} value={String(bayRow.master_bay_id)}>{String(bayRow.bay_name)}</option>)}</select></td><td><input type="number" min="1" value={Number(queueRow.queue_position || 1)} onChange={(event) => updateBayQueueRow(index, "queue_position", Number(event.target.value))} /></td><td><select value={String(queueRow.vehicle_id || "")} onChange={(event) => updateBayQueueRow(index, "vehicle_id", event.target.value)}>{vehicles.map((vehicle) => <option key={vehicle.mt_id} value={vehicle.mt_id}>{vehicle.registration || vehicle.mt_id}</option>)}</select></td><td><input value={String(queueRow.compartment_id || "")} onChange={(event) => updateBayQueueRow(index, "compartment_id", event.target.value || null)} /></td><td><select value={String(queueRow.product_id || "")} onChange={(event) => updateBayQueueRow(index, "product_id", event.target.value)}>{products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name}</option>)}</select></td><td><input type="number" min="1" value={Number(queueRow.estimated_loading_duration_minutes || 8)} onChange={(event) => updateBayQueueRow(index, "estimated_loading_duration_minutes", Number(event.target.value))} /></td><td><button className="phase7-link is-danger" onClick={() => setBay((current) => current ? { ...current, queue: current.queue.filter((_, rowIndex) => rowIndex !== index) } : current)}>Remove</button></td></tr>)}</tbody></table></div>}
          <div className="phase7-note"><Clock3 size={15} /> Current queue rows: {bay?.queue.length || 0}. Effective time follows the Initial/Re-optimize timestamp entered by the user, never the server clock.</div>
        </Section></div>
      </div>}

      {tab === "parameter" && <div className="phase7-grid-2">
        <Section title="Optimization Parameter Profile" description="Load, review, save a new version, or Save As. Every solver run copies an immutable effective snapshot." action={<div className="phase7-action-row"><button className="phase7-secondary" onClick={() => void saveProfile(false)}><Save size={15} /> Save</button><button className="phase7-secondary" onClick={() => void saveProfile(true)}>Save As</button></div>}>
          <label className="phase7-field"><span>Profile</span><select value={selectedProfile} onChange={(event) => setSelectedProfile(event.target.value)}>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_name} · v{profile.version}{profile.is_default ? " · Default" : ""}</option>)}</select></label>
          <p className="phase7-profile-description">{selectedProfileRow?.description}</p>
          <div className="phase7-form-grid">
            <label><span>Objective</span><select value={String(parameterDraft.objective || "MIN_TOTAL_COST")} onChange={(event) => setParameterDraft((current) => ({ ...current, objective: event.target.value }))}><option>MIN_TOTAL_COST</option><option>MIN_TOTAL_DISTANCE</option><option>MIN_TOTAL_OPERATING_TIME</option></select></label>
            {[ ["freeze_window_minutes", "Freeze Window (min)", 60], ["reoptimization_interval_minutes", "Reoptimization Interval", 60], ["route_optimization_time_limit", "Route Time Limit (sec)", 30], ["bay_optimization_time_limit", "Bay Time Limit (sec)", 30], ["bay_cp_sat_workers", "Bay CP-SAT Workers", 8], ["max_coordination_iterations", "Coordination Iterations", 5], ["departure_time_tolerance_minutes", "Departure Tolerance", 5], ["return_time_tolerance_minutes", "Return Tolerance", 5], ["maximum_trips_per_mt", "Maximum Trips / MT", 6], ["default_spbu_service_minutes", "SPBU Service Time", 30], ["gate_process_time", "Gate Process Time", 5] ].map(([key, label, fallback]) => <label key={String(key)}><span>{label}</span><input type="number" value={profileNumber(String(key), Number(fallback))} onChange={(event) => setParameterDraft((current) => ({ ...current, [String(key)]: Number(event.target.value) }))} /></label>)}
            <label><span>Route Vehicle Mode</span><select value={String(parameterDraft.route_vehicle_mode || "GENERAL_VEHICLE")} onChange={(event) => setParameterDraft((current) => ({ ...current, route_vehicle_mode: event.target.value }))}><option>GENERAL_VEHICLE</option><option>TRUCK</option></select></label>
            <label><span>Loading Mode</span><select value={String(parameterDraft.loading_mode || "SEQUENTIAL")} onChange={(event) => setParameterDraft((current) => ({ ...current, loading_mode: event.target.value }))}><option>SEQUENTIAL</option><option>PARALLEL</option></select></label>
            <label className="phase7-check"><input type="checkbox" checked={Boolean(parameterDraft.traffic_aware ?? true)} onChange={(event) => setParameterDraft((current) => ({ ...current, traffic_aware: event.target.checked }))} /><span>Traffic aware</span></label>
            <label className="phase7-check"><input type="checkbox" checked={Boolean(parameterDraft.route_matrix_cache_enabled ?? true)} onChange={(event) => setParameterDraft((current) => ({ ...current, route_matrix_cache_enabled: event.target.checked }))} /><span>Route matrix cache</span></label>
            <label><span>Cache TTL (min)</span><input type="number" value={profileNumber("route_matrix_cache_ttl_minutes", 60)} onChange={(event) => setParameterDraft((current) => ({ ...current, route_matrix_cache_ttl_minutes: Number(event.target.value) }))} /></label>
          </div>
          <div className="phase7-note"><MapPinned size={15} /> Google API key is managed in Google Maps Integration. GENERAL_VEHICLE uses DRIVE road data when configured; explicit TRUCK currently records a visibly labelled master-Haversine fallback because the configured client does not claim truck routing support.</div>
        </Section>
        <Section title="Operational Cost Controls" description="Base monetary weights remain separate from constraint penalties.">
          <div className="phase7-form-grid">{[["cost_per_km", "Cost / KM"], ["cost_per_operating_hour", "Operating / Hour"], ["queue_cost", "Queue / Minute"], ["loading_cost", "Loading / Minute"], ["overtime_cost", "Overtime / Minute"]].map(([key, label]) => <label key={key}><span>{label}</span><input type="number" min="0" value={profileNumber(key)} onChange={(event) => setParameterDraft((current) => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}</div>
        </Section>
        <div className="phase7-grid-span"><Section title="Constraint Settings · Hard / Soft / Disabled" description="Every listed business constraint can be enabled, disabled, or converted between HARD and SOFT. Penalty is applied only while the constraint is enabled as SOFT.">
          <div className="phase7-constraint-legend"><span><i className="is-hard" /> HARD: violation is rejected</span><span><i className="is-soft" /> SOFT: violation is allowed with penalty</span><span><i className="is-disabled" /> DISABLED: no enforcement and no penalty</span></div>
          <div className="phase7-table-wrap"><table className="phase7-table phase7-constraint-table"><thead><tr><th>Active</th><th>Constraint</th><th>Category</th><th>Mode</th><th>Time Limit</th><th>Penalty</th><th>Effective Behavior</th></tr></thead><tbody>{constraintCatalog.map((definition) => { const rule = constraintRuleFor(definition); const effectivePenalty = rule.enabled && rule.mode === "SOFT" ? rule.penalty : 0; return <tr key={definition.constraint_id} className={!rule.enabled ? "is-disabled" : ""}><td><label className="phase7-switch"><input type="checkbox" checked={rule.enabled} onChange={(event) => updateConstraintRule(definition, { enabled: event.target.checked })} /><span /></label></td><td><strong>{definition.label}</strong><small>{definition.description}</small><code>{definition.constraint_id}</code></td><td><span className="phase7-constraint-category">{definition.category}</span></td><td><select value={rule.mode} disabled={!rule.enabled} onChange={(event) => updateConstraintRule(definition, { mode: event.target.value as ConstraintRule["mode"] })}><option value="HARD">HARD</option><option value="SOFT">SOFT</option></select></td><td>{definition.default_limit_minutes ? <><input type="number" min="1" max="2880" value={rule.limit_minutes || definition.default_limit_minutes} onChange={(event) => updateConstraintRule(definition, { limit_minutes: Number(event.target.value) })} /><small>minutes from use until depot return</small></> : <span>—</span>}</td><td><input type="number" min="0" value={rule.penalty} disabled={!rule.enabled || rule.mode !== "SOFT"} onChange={(event) => updateConstraintRule(definition, { penalty: Number(event.target.value) })} /><small>Effective: {effectivePenalty.toLocaleString("id-ID")}</small></td><td><span className={`phase7-constraint-mode is-${!rule.enabled ? "disabled" : rule.mode.toLowerCase()}`}>{!rule.enabled ? "DISABLED" : rule.mode}</span><small>{!rule.enabled ? "Rule and penalty ignored" : rule.mode === "HARD" ? "Must pass; saved penalty ignored" : `Violation allowed · penalty ${rule.penalty.toLocaleString("id-ID")}`}</small></td></tr>; })}</tbody></table></div>
          <div className="phase7-note"><GitCompareArrows size={15} /> DONE/ONGOING execution state and persistence integrity remain non-configurable safeguards. Original Phase 6 prediction fields are never overwritten.</div>
        </Section></div>
        <div className="phase7-grid-span"><Section title="Vehicle Activation Cost Rules" description="Priority and tag specificity determine the matching activation cost for each used MT.">
          <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>Vehicle Class</th><th>Vehicle Tag</th><th>Activation Cost</th><th>Priority</th><th /></tr></thead><tbody>{(Array.isArray(parameterDraft.vehicle_activation_cost_rules) ? parameterDraft.vehicle_activation_cost_rules as Array<Record<string, unknown>> : []).map((rule, index) => <tr key={index}><td><input type="number" value={Number(rule.vehicle_class || 0)} onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, vehicle_class: Number(event.target.value) } : row) }))} /></td><td><input value={String(rule.vehicle_tag || "")} placeholder="Optional" onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, vehicle_tag: event.target.value || null } : row) }))} /></td><td><input type="number" value={Number(rule.activation_cost || 0)} onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, activation_cost: Number(event.target.value) } : row) }))} /></td><td><input type="number" value={Number(rule.priority || 0)} onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, priority: Number(event.target.value) } : row) }))} /></td><td><button className="phase7-link is-danger" onClick={() => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).filter((_, rowIndex) => rowIndex !== index) }))}>Remove</button></td></tr>)}</tbody></table></div>
          <button className="phase7-secondary" onClick={() => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: [...(current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []), { vehicle_class: 8, vehicle_tag: null, activation_cost: 500000, priority: 10 }] }))}><Plus size={15} /> Add Activation Rule</button>
        </Section></div>
      </div>}

      {tab === "route" && <div className="phase7-route-page"><Section title="Route Plan" description="Version-aware MT → Trip → SPBU sequence. MT filter and pagination are shared with Vehicle Multi-Trip Timeline." action={<div className="phase7-action-row"><select value={selectedVersion} onChange={(event) => void selectRouteVersion(event.target.value)}>{versions.map((version) => <option key={String(version.route_version_id)} value={String(version.route_version_id)}>{String(version.version_label)} · {String(version.reason)}</option>)}</select><select value={selectedMT} onChange={(event) => { setSelectedMT(event.target.value); setSelectedTrip("ALL"); setRouteMTPage(1); }}><option value="">All MT</option>{routeVehicleOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><select value={selectedTrip} onChange={(event) => setSelectedTrip(event.target.value === "ALL" ? "ALL" : Number(event.target.value))}><option value="ALL">All Trips</option>{Array.from(new Set((routeVersion?.trips || []).filter((row) => !selectedMT || row.vehicle_id === selectedMT).map((row) => row.trip_number))).map((number) => <option key={number} value={number}>Trip {number}</option>)}</select></div>}>
        {!routeVersion ? <EmptyState title="No route version" description="Run the initial optimization to create V1." /> : <>
          <div className="phase7-route-filters"><label><span>LO</span><input value={routeLOFilter} placeholder="Filter LO ID" onChange={(event) => setRouteLOFilter(event.target.value)} /></label><label><span>SPBU</span><input value={routeSPBUFilter} placeholder="Code or name" onChange={(event) => setRouteSPBUFilter(event.target.value)} /></label><label><span>Product</span><input value={routeProductFilter} placeholder="Product name or ID" onChange={(event) => setRouteProductFilter(event.target.value)} /></label></div>
          <KpiGrid values={[{ label: "Solver Status", value: routeVersion.solver_status }, { label: "First Loading", value: displayDateTime(routeVersion.first_loading_start) }, { label: "First Gate Out", value: displayDateTime(routeVersion.first_gate_out) }, { label: "Last Gate Out", value: displayDateTime(routeVersion.last_gate_out) }, { label: "Depot Operation Span", value: `${routeVersion.depot_dispatch_span_minutes} min` }]} />
          <div className="phase7-pagination"><span>Showing MT {routeMTRangeStart}–{routeMTRangeEnd} of {routeFilteredVehicleOptions.length}; the same MT are shown in both cards.</span><label><span>MT per page</span><select value={routeMTPerPage} onChange={(event) => setRouteMTPerPage(Number(event.target.value))}><option value={1}>1</option><option value={5}>5</option><option value={10}>10</option><option value={25}>25</option></select></label><div><button className="phase7-secondary" disabled={routeMTPage <= 1} onClick={() => setRouteMTPage((page) => Math.max(1, page - 1))}><ChevronLeft size={15} /> Previous</button><strong>Page {routeMTPage} of {routeMTPageCount}</strong><button className="phase7-secondary" disabled={routeMTPage >= routeMTPageCount} onClick={() => setRouteMTPage((page) => Math.min(routeMTPageCount, page + 1))}>Next <ChevronRight size={15} /></button></div></div>
          {!filteredTrips.length ? <EmptyState title="No matching route" description="Adjust the MT, Trip, LO, SPBU, or Product filters." /> : <div className="phase7-trip-stack">{filteredTrips.map((trip) => <div className="phase7-trip-card" key={trip.route_version_trip_id}>
            <div className="phase7-trip-head"><div><Truck size={18} /><strong>{trip.registration || trip.vehicle_id}</strong><span>Trip {trip.trip_number}</span><span>{trip.shipment_id}</span></div><span className={badgeClass(trip.assignment_status)}>{trip.assignment_status}</span></div>
            <div className="phase7-timeline"><span><small>READY</small>{displayDateTime(trip.vehicle_ready_at_depot)}</span><i /><span><small>QUEUE</small>{displayDateTime(trip.queue_start)}<small>{trip.queue_minutes} min</small></span><i /><span><small>LOADING</small>{displayDateTime(trip.loading_start)}<small>finish {displayDateTime(trip.loading_finish)}</small></span><i /><span><small>GATE OUT</small>{displayDateTime(trip.gate_out)}</span><i /><span><small>RETURN</small>{displayDateTime(trip.return_depot)}</span></div>
            <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>MT</th><th>Trip</th><th>Gate Out</th><th>LO</th><th>SPBU</th><th>Product</th><th>Volume</th><th>Sequence</th><th>ETA</th><th>ETD</th><th>Return Depot</th><th>Distance</th><th>Travel Time</th><th>Compartment</th><th>Frozen</th></tr></thead><tbody>{trip.loading_orders.filter(loMatchesRouteFilters).sort((a, b) => a.stop_sequence - b.stop_sequence).map((row) => { const stop = trip.stops.find((item) => item.sequence === row.stop_sequence); return <tr key={row.loading_order_id}><td>{trip.registration || trip.vehicle_id}</td><td>{trip.trip_number}</td><td>{displayDateTime(trip.gate_out)}</td><td>{row.loading_order_id}</td><td>{row.spbu_name || row.spbu_id}</td><td>{row.product_name || row.product_id || "—"}</td><td>{row.volume_kl} KL</td><td>{row.stop_sequence}</td><td>{displayDateTime(stop?.arrival_time || row.eta)}</td><td>{displayDateTime(stop?.departure_time)}</td><td>{displayDateTime(trip.return_depot)}</td><td>{stop ? `${(stop.distance_from_previous_meters / 1000).toFixed(1)} km` : "—"}</td><td>{stop ? `${Math.round(stop.travel_from_previous_seconds / 60)} min` : "—"}</td><td>{row.compartment_id}</td><td>{row.frozen ? "Yes" : "No"}</td></tr>; })}</tbody></table></div>
            <div className="phase7-trip-metrics"><span>{(trip.distance_meters / 1000).toFixed(1)} km</span><span>{Math.round(trip.travel_time_seconds / 60)} min driving</span><span>{trip.operating_minutes} min operating</span></div>
            {Array.isArray(trip.cost_breakdown.constraint_violations) && trip.cost_breakdown.constraint_violations.length > 0 && <div className="phase7-constraint-violations"><strong>Soft constraint violations</strong>{(trip.cost_breakdown.constraint_violations as Array<Record<string, unknown>>).map((violation, index) => <span key={`${String(violation.constraint_id)}-${index}`}>{String(violation.constraint_id).replace(/_/g, " ")} · penalty {Number(violation.penalty || 0).toLocaleString("id-ID")}</span>)}</div>}
          </div>)}</div>}
        </>}
      </Section>
      {routeVersion && <Section title="Vehicle Multi-Trip Timeline" description="Synchronized with the Route Plan MT dropdown and current MT pagination page.">
        <div className="phase7-vehicle-timeline">{tripsByMT.map(([vehicleId, row]) => <div key={vehicleId}><div className="phase7-vehicle-label"><Truck size={17} /><strong>{row.registration}</strong><small>{vehicleId}</small></div><div className="phase7-vehicle-events">{row.trips.map((trip) => <div key={trip.route_version_trip_id}><strong>Trip {trip.trip_number}</strong><span>Ready {displayDateTime(trip.vehicle_ready_at_depot)}</span><span>Queue {displayDateTime(trip.queue_start)}–{displayDateTime(trip.loading_start)}</span><span>Load {displayDateTime(trip.loading_start)}–{displayDateTime(trip.loading_finish)}</span><span>Gate Out {displayDateTime(trip.gate_out)}</span><span>Trip {displayDateTime(trip.gate_out)}–{displayDateTime(trip.return_depot)}</span><span>Return {displayDateTime(trip.return_depot)}</span></div>)}</div></div>)}</div>
      </Section>}
      </div>}

      {tab === "simulation" && <div className="phase7-grid-2">
        <Section title="Gate-Out KL per Hour" description="True hourly buckets, including zero-volume gaps, plus cumulative gate-out volume for the selected route version.">{routeVersion ? <ReactECharts style={{ height: 320 }} option={{ tooltip: { trigger: "axis" }, legend: { data: ["Hourly KL", "Cumulative KL"] }, xAxis: { type: "category", data: hourlySimulation.map((row) => row.label) }, yAxis: [{ type: "value", name: "KL" }], series: [{ name: "Hourly KL", type: "bar", data: hourlySimulation.map((row) => row.gateOutKL), itemStyle: { color: "#0b73bf" } }, { name: "Cumulative KL", type: "line", data: hourlySimulation.map((row) => row.cumulativeKL), smooth: true, itemStyle: { color: "#8aaa18" } }] }} /> : <EmptyState title="No simulation" description="Select or create a route version." />}</Section>
        <Section title="MT ETA Depot & Returning Capacity" description="Number of returning MT and total returning KL capacity aggregated per hour.">{routeVersion ? <ReactECharts style={{ height: 320 }} option={{ tooltip: { trigger: "axis" }, legend: { data: ["Returning MT", "Returning Capacity"] }, xAxis: { type: "category", data: hourlySimulation.map((row) => row.label) }, yAxis: [{ type: "value", name: "MT", minInterval: 1 }, { type: "value", name: "KL" }], series: [{ name: "Returning MT", type: "bar", data: hourlySimulation.map((row) => row.returningMT), itemStyle: { color: "#ea4a43" } }, { name: "Returning Capacity", type: "line", yAxisIndex: 1, data: hourlySimulation.map((row) => row.returningCapacityKL), itemStyle: { color: "#b8d211" } }] }} /> : <EmptyState title="No return projection" description="Select or create a route version." />}</Section>
        {routeVersion && <div className="phase7-grid-span"><Section title="Simulation KPI" description="Version-aware depot, fleet, and multi-trip performance."><KpiGrid values={[{ label: "First Loading", value: displayDateTime(routeVersion.first_loading_start) }, { label: "First Gate Out", value: displayDateTime(routeVersion.first_gate_out) }, { label: "Last Gate Out", value: displayDateTime(routeVersion.last_gate_out) }, { label: "Depot Operation Span", value: `${routeVersion.depot_dispatch_span_minutes} min` }, { label: "Max Trips / MT", value: routeVersion.summary.max_trips_per_mt || 0 }, { label: "Fleet Utilization", value: `${routeVersion.summary.fleet_utilization_pct || 0}%` }, { label: "Average Turnaround", value: `${routeVersion.summary.average_turnaround_minutes || routeVersion.summary.average_trip_duration_minutes || 0} min` }]} /></Section></div>}
        {routeVersion && <div className="phase7-grid-span"><OperationalKpiGroups routeVersion={routeVersion} /></div>}
      </div>}

      {tab === "map" && <Section title="Geographic Route Map" description="OR-Tools selects the plan. Cached Google Routes data supplies road distance/time/geometry when available; fallback geometry is visibly labelled." action={<div className="phase7-action-row"><select value={selectedVersion} onChange={(event) => void selectRouteVersion(event.target.value)}>{versions.map((version) => <option key={String(version.route_version_id)} value={String(version.route_version_id)}>{String(version.version_label)} · {String(version.reason)}</option>)}</select><select value={selectedMT} onChange={(event) => { setSelectedMT(event.target.value); setSelectedTrip("ALL"); }}><option value="">All MT</option>{routeVehicleOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><select value={selectedTrip} onChange={(event) => setSelectedTrip(event.target.value === "ALL" ? "ALL" : Number(event.target.value))}><option value="ALL">All Trips for selected MT</option>{Array.from(new Set((routeVersion?.trips || []).filter((row) => !selectedMT || row.vehicle_id === selectedMT).map((row) => row.trip_number))).map((number) => <option key={number} value={number}>Trip {number}</option>)}</select></div>}>
        {!selectedTrips.some((trip) => trip.route_geometry.length) ? <EmptyState title="No mappable geometry" description="Canonical coordinates or Google route geometry are missing for this selection." /> : <div className="phase7-map-grid">
          <MapContainer center={selectedTrips.find((trip) => trip.route_geometry.length)?.route_geometry[0] ? [selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].latitude, selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].longitude] : [-6.2, 106.8]} zoom={8} scrollWheelZoom className="phase7-map">
            <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            {selectedTrips.find((trip) => trip.route_geometry.length)?.route_geometry[0] && <CircleMarker center={[selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].latitude, selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].longitude]} radius={9} pathOptions={{ color: "#15385b", fillColor: "#fff", fillOpacity: 1, weight: 3 }}><Popup><strong>Depot</strong><br />Start and return point for the selected routes.</Popup></CircleMarker>}
            {selectedTrips.map((trip, index) => { const positions = trip.route_geometry.map((point) => [point.latitude, point.longitude] as [number, number]); const color = ["#0b73bf", "#ea4a43", "#8aaa18", "#7c3aed"][index % 4]; return <span key={trip.route_version_trip_id}>{positions.length > 1 && <Polyline positions={positions} pathOptions={{ color, weight: 4, dashArray: trip.route_geometry_source === "GOOGLE_ROUTES_GEOJSON" ? undefined : "8 8" }} />}{trip.stops.filter((stop) => stop.latitude !== null && stop.longitude !== null).map((stop) => <CircleMarker key={`${trip.route_version_trip_id}-${stop.sequence}`} center={[stop.latitude!, stop.longitude!]} radius={7} pathOptions={{ color, fillColor: color, fillOpacity: 0.9 }}><Popup><strong>{trip.registration} · Trip {trip.trip_number}</strong><br />Stop {stop.sequence} · {stop.spbu_name}<br />LO {stop.loading_order_ids.join(", ")}<br />Product {stop.product_names?.join(", ") || stop.products.filter(Boolean).join(", ") || "—"}<br />Volume {stop.volume_kl} KL<br />ETA {displayDateTime(stop.arrival_time)}<br />Distance {(stop.distance_from_previous_meters / 1000).toFixed(1)} km<br />Travel {Math.round(stop.travel_from_previous_seconds / 60)} min</Popup></CircleMarker>)}</span>; })}
          </MapContainer>
          <div className="phase7-map-legend">{selectedTrips.map((trip) => <div key={trip.route_version_trip_id}><strong>{trip.registration} · Trip {trip.trip_number}</strong><span>{trip.stops.map((stop) => `${stop.sequence}. ${stop.spbu_name}`).join(" → ")}</span><small>{(trip.distance_meters / 1000).toFixed(1)} km · {Math.round(trip.travel_time_seconds / 60)} min · {trip.route_geometry_source}</small></div>)}</div>
        </div>}
      </Section>}

      {tab === "cost" && <div className="phase7-grid-2">
        <Section title="Total Cost" description="Activation + distance + operating + queue + loading + overtime + penalties.">{routeVersion ? <><KpiGrid values={[{ label: "Total Cost", value: `Rp ${(routeVersion.cost.total_cost || 0).toLocaleString("id-ID")}` }, { label: "Cost / MT", value: `Rp ${(routeVersion.cost.cost_per_mt || 0).toLocaleString("id-ID")}` }, { label: "Cost / Trip", value: `Rp ${(routeVersion.cost.cost_per_trip || 0).toLocaleString("id-ID")}` }, { label: "Cost / KM", value: `Rp ${(routeVersion.cost.cost_per_km || 0).toLocaleString("id-ID")}` }, { label: "Cost / KL", value: `Rp ${(routeVersion.cost.cost_per_kl || 0).toLocaleString("id-ID")}` }, { label: "Cost / LO", value: `Rp ${(routeVersion.cost.cost_per_lo || 0).toLocaleString("id-ID")}` }]} /><div className="phase7-cost-bars">{[["Vehicle Activation", "vehicle_activation_cost"], ["Distance", "distance_cost"], ["Operating Time", "operating_time_cost"], ["Queue", "queue_cost"], ["Loading", "loading_cost"], ["Overtime", "overtime_cost"], ["Penalty", "penalty_cost"]].map(([label, key]) => <div key={key}><span>{label}</span><strong>Rp {(routeVersion.cost[key] || 0).toLocaleString("id-ID")}</strong><i style={{ width: `${Math.min(100, (routeVersion.cost[key] || 0) / Math.max(1, routeVersion.cost.total_cost || 1) * 100)}%` }} /></div>)}</div></> : <EmptyState title="No cost result" description="Cost is calculated for every route version." />}</Section>
        <Section title="Dropped / Unserved LO" description="Infeasible LO is explicit and narrative—never silently omitted.">{!routeVersion ? <EmptyState title="No route result" description="Create or select a route version." /> : !routeVersion.dropped_lo.length ? <div className="phase7-note"><CheckCircle2 size={16} /> No dropped LO in {routeVersion.version_label}.</div> : <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>LO ID</th><th>SPBU</th><th>Product</th><th>Volume</th><th>Reason Code</th><th>Description</th><th>Route Version</th></tr></thead><tbody>{routeVersion.dropped_lo.map((row) => <tr key={String(row.loading_order_id)}><td>{String(row.loading_order_id)}</td><td>{String(row.spbu || row.spbu_id)}</td><td>{String(row.product_id || "—")}</td><td>{String(row.volume_kl)} KL</td><td><span className="phase7-badge is-bad">{String(row.reason_code)}</span></td><td>{String(row.reason_description)}</td><td>{routeVersion.version_label}</td></tr>)}</tbody></table></div>}</Section>
        {routeVersion && <div className="phase7-grid-span"><Section title="Cost Drilldown by MT and Trip" description="Trip costs are stored on the route version. Unserved LO penalties remain visible at plan level and are not attributed to an arbitrary MT.">
          <h4 className="phase7-subheading">Per MT</h4><div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>MT</th><th>Trips</th><th>Distance</th><th>Operating</th><th>Volume</th><th>Trip-attributed Cost</th></tr></thead><tbody>{costByMT.map(([vehicleId, row]) => <tr key={vehicleId}><td><strong>{row.registration}</strong><small>{vehicleId}</small></td><td>{row.trips}</td><td>{(row.distance / 1000).toFixed(1)} km</td><td>{row.operating} min</td><td>{row.volume.toFixed(1)} KL</td><td>Rp {row.cost.toLocaleString("id-ID")}</td></tr>)}</tbody></table></div>
          <h4 className="phase7-subheading">Per Trip</h4><div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>MT</th><th>Trip</th><th>Activation</th><th>Distance</th><th>Operating</th><th>Queue</th><th>Loading</th><th>Overtime</th><th>Penalty</th><th>Total</th></tr></thead><tbody>{routeVersion.trips.map((trip) => <tr key={trip.route_version_trip_id}><td>{trip.registration || trip.vehicle_id}</td><td>{trip.trip_number}</td>{["vehicle_activation_cost", "distance_cost", "operating_time_cost", "queue_cost", "loading_cost", "overtime_cost", "penalty_cost", "total_cost"].map((key) => <td key={key}>Rp {Number(trip.cost_breakdown?.[key] || 0).toLocaleString("id-ID")}</td>)}</tr>)}</tbody></table></div>
        </Section></div>}
      </div>}

      {tab === "versions" && <Section title="Versions / Audit History" description="Every optimization is append-only. V1 is baseline; latest is the current operational plan." action={<select value={selectedVersion} onChange={(event) => void selectRouteVersion(event.target.value)}>{versions.map((version) => <option key={String(version.route_version_id)} value={String(version.route_version_id)}>{String(version.version_label)} · {String(version.reason)}</option>)}</select>}>
        {!versions.length ? <EmptyState title="No route history" description="The first optimization creates immutable V1." /> : <div className="phase7-version-grid">{versions.map((version) => <button key={String(version.route_version_id)} className={selectedVersion === version.route_version_id ? "is-active" : ""} onClick={() => void selectRouteVersion(String(version.route_version_id))}><span>{String(version.version_label)}</span><strong>{String(version.reason)}</strong><small>Reference: {displayDateTime(String(version.optimization_reference_time || ""), job.depot_timezone)}</small><small>Persisted: {displayDateTime(String(version.created_at))}</small><i className={badgeClass(String(version.solver_status))}>{String(version.solver_status)}</i></button>)}</div>}
        {routeVersion && <div className="phase7-audit"><div><h4>{routeVersion.version_label} audit inputs</h4><span>Parameter checksum: <code>{routeVersion.parameter_checksum}</code></span></div>{Boolean(routeVersion.parameter_snapshot?.constraint_rules) && <div className="phase7-audit-constraints">{Object.entries(routeVersion.parameter_snapshot.constraint_rules as Record<string, ConstraintRule>).map(([constraintId, rule]) => <span key={constraintId}><strong>{constraintCatalog.find((definition) => definition.constraint_id === constraintId)?.label || constraintId}</strong><i className={`phase7-constraint-mode is-${!rule.enabled ? "disabled" : rule.mode.toLowerCase()}`}>{!rule.enabled ? "DISABLED" : rule.mode}</i><small>Configured {Number(rule.penalty || 0).toLocaleString("id-ID")} · Effective {rule.enabled && rule.mode === "SOFT" ? Number(rule.penalty || 0).toLocaleString("id-ID") : "0"}</small></span>)}</div>}{routeVersion.audit_events.length ? routeVersion.audit_events.map((event, index) => <div className="phase7-audit-event" key={index}><History size={15} /><pre>{JSON.stringify(event, null, 2)}</pre></div>) : <div className="phase7-note">Baseline or no actual operational changes recorded before this version.</div>}<KpiGrid values={[{ label: "Plan Adherence", value: `${routeVersion.comparison.plan_adherence_pct ?? 100}%` }, { label: "Vehicle Changes", value: Number(routeVersion.comparison.vehicle_assignment_changes || 0) }, { label: "Shipment Changes", value: Number(routeVersion.comparison.shipment_changes || 0) }, { label: "Gate-Out Variance", value: `${routeVersion.comparison.gate_out_variance_minutes || 0} min` }]} /></div>}
      </Section>}

      {deleteBayTarget && <div className="phase7-modal-backdrop"><div className="phase7-modal" role="alertdialog" aria-modal="true" aria-labelledby="phase7-delete-bay-title"><button type="button" className="phase7-modal-close" disabled={busy} onClick={() => setDeleteBayTarget(null)} aria-label="Close delete Bay dialog"><X size={18} /></button><span className="phase7-overline">Delete Loading Bay</span><h3 id="phase7-delete-bay-title">Hapus {String(deleteBayTarget.bay_name || deleteBayTarget.bay_id || "Bay")}?</h3><div className="phase7-delete-warning"><AlertTriangle size={20} /><div><strong>Bay akan dikeluarkan dari konfigurasi aktif depot.</strong><span>Riwayat route dan bay assignment lama tetap dipertahankan untuk audit. Pastikan bay ini tidak lagi dipakai dalam kondisi operasional saat ini.</span></div></div><div className="phase7-action-row is-modal-actions"><button className="phase7-secondary" disabled={busy} onClick={() => setDeleteBayTarget(null)}>Batal</button><button className="phase7-delete-confirm" disabled={busy} onClick={() => void deleteSelectedBay()}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : <Trash2 size={16} />} Delete Bay</button></div></div></div>}
      {plannedETADemoDialog && <div className="phase7-modal-backdrop"><div className="phase7-modal" role="dialog" aria-modal="true" aria-labelledby="phase7-planned-eta-demo-title"><button type="button" className="phase7-modal-close" disabled={busy} onClick={() => setPlannedETADemoDialog(null)} aria-label="Close Planned ETA Depot Demo dialog"><X size={18} /></button><span className="phase7-overline">MT Management Demo</span><h3 id="phase7-planned-eta-demo-title">Planned ETA Depot Demo</h3><p className="phase7-modal-intro">Tanggal dan waktu ini akan mengganti Planned ETA Depot untuk seluruh {vehicles.length} MT pada Job ini.</p><div className="phase7-reference-grid"><label><span>Tanggal Planned ETA</span><input type="date" value={plannedETADemoDialog.date} disabled={busy} onChange={(event) => setPlannedETADemoDialog((current) => current ? { ...current, date: event.target.value } : current)} /></label><label><span>Waktu Planned ETA</span><input type="time" step="60" value={plannedETADemoDialog.time} disabled={busy} onChange={(event) => setPlannedETADemoDialog((current) => current ? { ...current, time: event.target.value } : current)} /></label></div><div className="phase7-reference-summary"><span><small>MT yang diperbarui</small><strong>{vehicles.length} MT</strong></span><span><small>Timezone Depot</small><strong>{job.depot_timezone}</strong></span><span><small>Operating Date Job</small><strong>{job.operating_date}</strong></span></div><div className="phase7-reference-impact"><strong>Prioritas Effective ETA:</strong><span>User ETA Override yang sudah terisi tetap memiliki prioritas, kemudian System ETA, baru Planned ETA Depot ini.</span></div><div className="phase7-action-row is-modal-actions"><button type="button" className="phase7-secondary" disabled={busy} onClick={() => setPlannedETADemoDialog(null)}>Batal</button><button type="button" className="phase7-primary" disabled={busy || !plannedETADemoDialog.date || !plannedETADemoDialog.time} onClick={() => void applyPlannedETADemo()}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : <CalendarClock size={16} />} Terapkan ke Semua MT</button></div></div></div>}
      {readinessOpen && validation && <div className="phase7-modal-backdrop"><div className="phase7-modal is-readiness" role="dialog" aria-modal="true" aria-labelledby="phase7-readiness-title"><button type="button" className="phase7-modal-close" onClick={() => setReadinessOpen(false)} aria-label="Close optimization readiness dialog"><X size={18} /></button><span className="phase7-overline">Pre-Optimization Validation</span><h3 id="phase7-readiness-title">Optimization Readiness</h3><p className="phase7-modal-intro">Hasil ini dihitung dari LO, MT, bay state, loading duration, dan draft constraint yang sedang aktif.</p><div className="phase7-readiness-summary"><span className={badgeClass(validation.status)}>{validation.status}</span><strong>{validation.status === "BLOCKED" ? "Perbaiki semua blocker sebelum menjalankan optimization." : validation.status === "WARNING" ? "Optimization dapat dilanjutkan setelah warning ditinjau." : "Input siap digunakan untuk optimization."}</strong></div><div className="phase7-validation">{validation.messages.map((message) => <div key={message.code} className={`phase7-validation-row is-${message.level.toLowerCase()}`}><strong>{message.code}</strong><span>{message.message}</span></div>)}</div><div className="phase7-action-row is-modal-actions"><button type="button" className="phase7-primary" onClick={() => setReadinessOpen(false)}>Tutup</button></div></div></div>}
      {optimizationDialog && <div className="phase7-modal-backdrop"><div className="phase7-modal is-optimization" role="dialog" aria-modal="true" aria-labelledby="phase7-optimization-time-title"><button type="button" className="phase7-modal-close" disabled={busy} onClick={() => setOptimizationDialog(null)} aria-label="Close optimization time dialog"><X size={18} /></button><span className="phase7-overline">{optimizationDialog.reroute ? "Re-Optimize Operational Plan" : "Initial Optimization"}</span><h3 id="phase7-optimization-time-title">Tentukan tanggal dan waktu optimasi</h3><p className="phase7-modal-intro">Waktu ini bukan sekadar waktu pencatatan. Backend menggunakannya sebagai patokan availability MT, Bay State Effective, waktu departure dan route, serta freeze window saat Re-optimize.</p><div className="phase7-reference-grid"><label><span>Tanggal Optimasi</span><input type="date" value={optimizationDialog.date} disabled={optimizationDialog.reroute || busy} onChange={(event) => setOptimizationDialog((current) => current ? { ...current, date: event.target.value } : current)} /></label><label><span>Waktu Optimasi</span><input type="time" step="60" value={optimizationDialog.time} disabled={busy} onChange={(event) => setOptimizationDialog((current) => current ? { ...current, time: event.target.value } : current)} /></label></div><div className="phase7-reference-summary"><span><small>Timezone Depot</small><strong>{job.depot_timezone}</strong></span><span><small>Operating Date Job</small><strong>{job.operating_date}</strong></span><span><small>Initial Reference</small><strong>{job.initial_optimization_reference_time ? displayDateTime(job.initial_optimization_reference_time, job.depot_timezone) : "Belum ada"}</strong></span><span><small>Latest Reference</small><strong>{job.latest_optimization_reference_time ? displayDateTime(job.latest_optimization_reference_time, job.depot_timezone) : "Belum ada"}</strong></span></div><div className="phase7-reference-impact">{optimizationDialog.reroute ? <><strong>Patokan freeze dan bay:</strong><span>DONE dan ONGOING tetap frozen. PLANNED di dalam window mengikuti mode Freeze Window, sedangkan state/queue bay berlaku tepat pada waktu reroute ini.</span></> : <><strong>Patokan availability, bay, dan route:</strong><span>Route tidak dimulai sebelum waktu referensi ini. State/queue bay juga berlaku pada waktu ini; ETA MT dan jam operasional mengikuti mode constraint yang dipilih.</span></>}</div>{(optimizationDialogError || error) && <div className="phase7-inline-alert is-error"><AlertTriangle size={17} />{optimizationDialogError || error}</div>}<div className="phase7-action-row is-modal-actions"><button type="button" className="phase7-secondary" disabled={busy} onClick={() => setOptimizationDialog(null)}>Batal</button><button type="button" className="phase7-primary" disabled={busy || Boolean(optimizationDialogError)} onClick={() => void optimize(optimizationDialog.reroute, `${optimizationDialog.date}T${optimizationDialog.time}:00`)}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : optimizationDialog.reroute ? <RefreshCw size={16} /> : <Play size={16} />}{optimizationDialog.reroute ? "Jalankan Re-Optimize" : "Jalankan Initial Optimization"}</button></div></div></div>}
    </div>
  );
}
