import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import {
  AlertTriangle,
  Boxes,
  CalendarClock,
  CheckCircle2,
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
  Settings2,
  Truck,
  Warehouse,
  X,
} from "lucide-react";
import { apiGet, apiSend } from "../lib/api";


type Depot = { depot_id: string; depot_code: string | null; depot_name: string };
type Product = { product_id: string; product_name: string; active_status?: string };
type Phase7Tab = "overview" | "lo" | "mt" | "bay" | "parameter" | "route" | "simulation" | "map" | "cost" | "versions";
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
type JobDetail = JobSummary & {
  header: Record<string, string | null>;
  kpis: Record<string, number>;
  source_prediction_run_id: string | null;
  depot_operational_start: string;
  depot_operational_end: string;
  error_message?: string | null;
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
  cost_breakdown: Record<string, number>;
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
    distance_from_previous_meters: number;
    travel_from_previous_seconds: number;
  }>;
  loading_orders: Array<{
    loading_order_id: string;
    spbu_id: string;
    spbu_name: string | null;
    product_id: string | null;
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
  first_gate_out: string | null;
  last_gate_out: string | null;
  depot_dispatch_span_minutes: number;
  summary: Record<string, number | string | null>;
  cost: Record<string, number>;
  comparison: Record<string, number | string | boolean>;
  audit_events: Array<Record<string, unknown>>;
  parameter_checksum: string | null;
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


function displayDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("id-ID", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}


function toLocalInput(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
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


export function Phase7OptimizationPage({ depots, products }: { depots: Depot[]; products: Product[] }) {
  const [selectedDepot, setSelectedDepot] = useState("");
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [tab, setTab] = useState<Phase7Tab>("overview");
  const [loadingOrders, setLoadingOrders] = useState<LoadingOrder[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [predictionRuns, setPredictionRuns] = useState<PredictionRun[]>([]);
  const [selectedPredictionRun, setSelectedPredictionRun] = useState("");
  const [profiles, setProfiles] = useState<ParameterProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [parameterDraft, setParameterDraft] = useState<Record<string, unknown>>({});
  const [versions, setVersions] = useState<Array<Record<string, unknown>>>([]);
  const [selectedVersion, setSelectedVersion] = useState("");
  const [routeVersion, setRouteVersion] = useState<RouteVersion | null>(null);
  const [bay, setBay] = useState<BayPayload | null>(null);
  const [selectedLO, setSelectedLO] = useState<Set<string>>(new Set());
  const [bulkLOStatus, setBulkLOStatus] = useState<LoadingOrder["status"]>("ONGOING");
  const [vehicleDrafts, setVehicleDrafts] = useState<Record<string, { planned: string; override: string; status: string }>>({});
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState({ operating_date: new Date().toISOString().slice(0, 10), job_name: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [validation, setValidation] = useState<{ status: string; messages: Array<{ code: string; level: string; message: string }> } | null>(null);
  const [selectedMT, setSelectedMT] = useState("");
  const [selectedTrip, setSelectedTrip] = useState<number | "ALL">("ALL");
  const [routeLOFilter, setRouteLOFilter] = useState("");
  const [routeSPBUFilter, setRouteSPBUFilter] = useState("");
  const [routeProductFilter, setRouteProductFilter] = useState("");

  async function loadJobs(depotId: string) {
    if (!depotId) { setJobs([]); return; }
    try { setJobs(await apiGet<JobSummary[]>(`/api/v1/phase7/jobs?depot_id=${encodeURIComponent(depotId)}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to load Phase 7 jobs."); }
  }

  async function loadWorkspace(jobId: string, versionId?: string) {
    const [jobPayload, loPayload, vehiclePayload, runPayload, profilePayload, versionPayload, bayPayload] = await Promise.all([
      apiGet<JobDetail>(`/api/v1/phase7/jobs/${jobId}`),
      apiGet<LoadingOrder[]>(`/api/v1/phase7/jobs/${jobId}/loading-orders`),
      apiGet<Vehicle[]>(`/api/v1/phase7/jobs/${jobId}/vehicles`),
      apiGet<PredictionRun[]>(`/api/v1/phase7/jobs/${jobId}/prediction-runs`),
      apiGet<ParameterProfile[]>("/api/v1/phase7/parameter-profiles"),
      apiGet<Array<Record<string, unknown>>>(`/api/v1/phase7/jobs/${jobId}/versions`),
      apiGet<BayPayload>(`/api/v1/phase7/jobs/${jobId}/bay-state`),
    ]);
    setJob(jobPayload);
    setLoadingOrders(loPayload);
    setVehicles(vehiclePayload);
    setPredictionRuns(runPayload);
    setProfiles(profilePayload);
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

  useEffect(() => {
    const profile = profiles.find((row) => row.profile_id === selectedProfile);
    if (profile) setParameterDraft(profile.parameters);
  }, [selectedProfile, profiles]);

  async function runAction(action: () => Promise<void>, success: string) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Phase 7 action failed."); }
    finally { setBusy(false); }
  }

  async function openJob(jobId: string) {
    await runAction(async () => { await loadWorkspace(jobId); setTab("overview"); }, "Job workspace loaded.");
  }

  const selectedRun = predictionRuns.find((row) => row.id === selectedPredictionRun || row.run_id === selectedPredictionRun);
  const selectedProfileRow = profiles.find((row) => row.profile_id === selectedProfile);
  const routeVehicleOptions = useMemo(() => Array.from(new Map((routeVersion?.trips || []).map((row) => [row.vehicle_id, row.registration || row.vehicle_id])).entries()), [routeVersion]);
  const loMatchesRouteFilters = (row: RouteTrip["loading_orders"][number]) =>
    (!routeLOFilter || row.loading_order_id.toLocaleLowerCase("id-ID").includes(routeLOFilter.toLocaleLowerCase("id-ID")))
    && (!routeSPBUFilter || `${row.spbu_id} ${row.spbu_name || ""}`.toLocaleLowerCase("id-ID").includes(routeSPBUFilter.toLocaleLowerCase("id-ID")))
    && (!routeProductFilter || String(row.product_id || "").toLocaleLowerCase("id-ID").includes(routeProductFilter.toLocaleLowerCase("id-ID")));
  const selectedTrips = useMemo(() => (routeVersion?.trips || []).filter((trip) =>
    (!selectedMT || trip.vehicle_id === selectedMT)
    && (selectedTrip === "ALL" || trip.trip_number === selectedTrip)),
  [routeVersion, selectedMT, selectedTrip]);
  const filteredTrips = useMemo(() => selectedTrips.filter((trip) =>
    (!routeLOFilter && !routeSPBUFilter && !routeProductFilter) || trip.loading_orders.some(loMatchesRouteFilters),
  ), [selectedTrips, routeLOFilter, routeSPBUFilter, routeProductFilter]);
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
  const tripsByMT = useMemo(() => {
    const grouped = new Map<string, { registration: string; trips: RouteTrip[] }>();
    for (const trip of routeVersion?.trips || []) {
      const current = grouped.get(trip.vehicle_id) || { registration: trip.registration || trip.vehicle_id, trips: [] };
      current.trips.push(trip);
      current.trips.sort((a, b) => a.trip_number - b.trip_number);
      grouped.set(trip.vehicle_id, current);
    }
    return Array.from(grouped.entries());
  }, [routeVersion]);
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

  async function createNewJob() {
    if (!selectedDepot || !createForm.job_name.trim()) return;
    await runAction(async () => {
      const created = await apiSend<JobDetail>("/api/v1/phase7/jobs", "POST", { depot_id: selectedDepot, ...createForm });
      setCreateOpen(false); await loadJobs(selectedDepot); await loadWorkspace(created.job_id); setTab("overview");
    }, "New Phase 7 Job created.");
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

  async function saveActualBayState() {
    if (!job || !bay) return;
    await runAction(async () => {
      const now = new Date().toISOString();
      const states = bay.configuration.bays.map((row) => {
        const existing = bay.states.find((state) => state.master_bay_id === row.master_bay_id);
        return { master_bay_id: row.master_bay_id, current_vehicle_id: existing?.current_vehicle_id || null, current_compartment_id: existing?.current_compartment_id || null, current_product_id: existing?.current_product_id || null, remaining_loading_minutes: Number(existing?.remaining_loading_minutes || 0), actual_queue_length: Number(existing?.actual_queue_length || 0), state_effective_at: existing?.state_effective_at || now };
      });
      await apiSend(`/api/v1/phase7/jobs/${job.job_id}/bay-state`, "PUT", { states, queue: bay.queue });
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
          state_effective_at: new Date().toISOString(),
        },
      ],
    });
  }

  function updateBayQueueRow(index: number, field: string, value: unknown) {
    setBay((current) => current ? { ...current, queue: current.queue.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row) } : current);
  }

  async function validateWorkspace() {
    if (!job) return;
    await runAction(async () => { const result = await apiGet<typeof validation>(`/api/v1/phase7/jobs/${job.job_id}/validation`); setValidation(result); await refreshWorkspace(); }, "Pre-optimization validation completed.");
  }

  async function optimize(reroute: boolean) {
    if (!job) return;
    await runAction(async () => {
      const result = await apiSend<RouteVersion>(`/api/v1/phase7/jobs/${job.job_id}/${reroute ? "reroute" : "optimize"}`, "POST", { profile_id: selectedProfile || null, parameters: parameterDraft, current_time: new Date().toISOString(), reason: reroute ? "Operational Reroute" : "Initial Plan" });
      await loadWorkspace(job.job_id, result.route_version_id); setTab("route");
    }, reroute ? "New immutable reroute version created." : "Baseline route V1 created.");
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
          <button className="phase7-primary" disabled={!selectedDepot} onClick={() => setCreateOpen(true)}><Plus size={16} /> Create New Job</button>
        </div>
        <Section title="Job Management" description="Create or open an immutable-versioned operational workspace.">
          <div className="phase7-filter-row">
            <label><span>Depot</span><select value={selectedDepot} onChange={(event) => setSelectedDepot(event.target.value)}><option value="">Select Depot</option>{depots.map((depot) => <option key={depot.depot_id} value={depot.depot_id}>{depot.depot_name}</option>)}</select></label>
            <button className="phase7-secondary" disabled={!selectedDepot} onClick={() => void loadJobs(selectedDepot)}><RefreshCw size={15} /> Refresh</button>
          </div>
          {!selectedDepot ? <EmptyState title="Select Depot" description="The job list appears only after a depot is selected." /> : jobs.length === 0 ? <EmptyState title="No Phase 7 Job" description="Create a job for this depot and operating date." /> : (
            <div className="phase7-table-wrap"><table className="phase7-table"><thead><tr><th>Job ID</th><th>Operating Date</th><th>Depot</th><th>Total LO</th><th>Total MT</th><th>Current Route</th><th>Status</th><th>Last Updated</th><th>Action</th></tr></thead><tbody>{jobs.map((row) => <tr key={row.job_id}><td><strong>{row.job_no}</strong><small>{row.job_name}</small></td><td>{row.operating_date}</td><td>{row.depot}</td><td>{row.total_lo}</td><td>{row.total_mt}</td><td>{row.current_route_version || "—"}</td><td><span className={badgeClass(row.status)}>{row.status}</span></td><td>{displayDateTime(row.last_updated)}</td><td><button className="phase7-link" onClick={() => void openJob(row.job_id)}>Open Job <ChevronRight size={14} /></button></td></tr>)}</tbody></table></div>
          )}
        </Section>
        {createOpen && <div className="phase7-modal-backdrop"><div className="phase7-modal"><button className="phase7-modal-close" onClick={() => setCreateOpen(false)}><X size={18} /></button><span className="phase7-overline">Create New Job</span><h3>New operational workspace</h3><label><span>Depot</span><select value={selectedDepot} disabled><option value={selectedDepot}>{depots.find((row) => row.depot_id === selectedDepot)?.depot_name}</option></select></label><label><span>Operating Date</span><input type="date" value={createForm.operating_date} onChange={(event) => setCreateForm((current) => ({ ...current, operating_date: event.target.value }))} /></label><label><span>Job Name</span><input value={createForm.job_name} maxLength={255} placeholder="Morning dispatch control" onChange={(event) => setCreateForm((current) => ({ ...current, job_name: event.target.value }))} /></label><button className="phase7-primary" disabled={busy || !createForm.job_name.trim()} onClick={() => void createNewJob()}>{busy ? <LoaderCircle className="animate-spin" size={16} /> : <Plus size={16} />} Create & Open Workspace</button></div></div>}
        {(error || notice) && <div className={`phase7-toast ${error ? "is-error" : "is-success"}`}>{error || notice}</div>}
      </div>
    );
  }

  const headerKpis = ["total_lo", "done_lo", "ongoing_lo", "planned_lo", "dropped_lo", "used_mt", "total_trips", "delivered_kl", "remaining_kl"].map((key) => ({ label: key.replace(/_/g, " "), value: job.kpis[key] ?? 0 }));
  const profileNumber = (key: string, fallback = 0) => Number(parameterDraft[key] ?? fallback);

  return (
    <div className="phase7-shell">
      <div className="phase7-job-header">
        <div className="phase7-job-topline"><button className="phase7-link" onClick={() => { setJob(null); setRouteVersion(null); void loadJobs(selectedDepot); }}>← Job Management</button><span className={badgeClass(job.status)}>{job.status}</span></div>
        <div className="phase7-job-identity"><div><span>Job ID</span><strong>{job.job_no}</strong></div><div><span>Depot</span><strong>{job.depot}</strong></div><div><span>Operating Date</span><strong>{job.operating_date}</strong></div><div><span>Source Phase 6 Run</span><strong>{job.header.source_phase6_run_id || "Not loaded"}</strong></div><div><span>Current Route Version</span><strong>{job.current_route_version || "—"}</strong></div></div>
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
        <Section title="Optimization Readiness" description="Visible pre-solver gate with READY, WARNING, or BLOCKED messages." action={<button className="phase7-secondary" onClick={() => void validateWorkspace()}><RefreshCw size={15} /> Validate</button>}>
          {validation ? <div className="phase7-validation"><span className={badgeClass(validation.status)}>{validation.status}</span>{validation.messages.map((message) => <div key={message.code} className={`phase7-validation-row is-${message.level.toLowerCase()}`}><strong>{message.code}</strong><span>{message.message}</span></div>)}</div> : <EmptyState title="Validation not run" description="Complete LO, MT, bay, loading duration, and parameter inputs, then validate." />}
        </Section>
        <Section title="Initial Optimization Flow" description="Every completed run creates an immutable route version and parameter snapshot.">
          <div className="phase7-step-list">{["Load Phase 6 Prediction Run", "Load MT from Master", "Enter initial MT ETA Depot", "Enter current Bay State", "Select and review Parameter Profile", "Validate hard requirements", "Run Initial Optimization → V1"].map((label, index) => <div key={label}><span>{index + 1}</span><strong>{label}</strong></div>)}</div>
          <div className="phase7-action-row"><button className="phase7-primary" disabled={busy || !job.source_prediction_run_id} onClick={() => void optimize(false)}><Play size={16} /> Run Initial Optimization</button><button className="phase7-secondary" disabled={busy || !job.current_route_version_id} onClick={() => void optimize(true)}><RefreshCw size={16} /> Re-Optimize Now</button></div>
        </Section>
        <Section title="Current Plan Stability" description="V1 is baseline; the latest route version is the current operational plan.">
          {routeVersion ? <KpiGrid values={[{ label: "Plan Adherence", value: `${routeVersion.comparison.plan_adherence_pct ?? 100}%` }, { label: "Vehicle Changes", value: Number(routeVersion.comparison.vehicle_assignment_changes || 0) }, { label: "Shipment Changes", value: Number(routeVersion.comparison.shipment_changes || 0) }, { label: "Gate-Out Variance", value: `${routeVersion.comparison.gate_out_variance_minutes || 0} min` }]} /> : <EmptyState title="No baseline version" description="The first successful optimization creates V1." />}
        </Section>
        {routeVersion && <Section title="Operational KPI Groups" description="Complete version-aware LO, fleet, multi-trip, bay, route, cost, and reroute health indicators.">
          <div className="phase7-kpi-groups">
            <div><h4>LO</h4><KpiGrid values={[{ label: "Total LO", value: routeVersion.summary.total_lo || 0 }, { label: "Done LO", value: routeVersion.summary.done_lo || 0 }, { label: "Ongoing LO", value: routeVersion.summary.ongoing_lo || 0 }, { label: "Planned LO", value: routeVersion.summary.planned_lo || 0 }, { label: "Dropped LO", value: routeVersion.summary.dropped_lo || 0 }, { label: "Completion", value: `${routeVersion.summary.completion_pct || 0}%` }, { label: "Delivered", value: `${routeVersion.summary.delivered_kl || 0} KL` }, { label: "Remaining", value: `${routeVersion.summary.remaining_kl || 0} KL` }]} /></div>
            <div><h4>Fleet</h4><KpiGrid values={[{ label: "Available MT", value: routeVersion.summary.available_mt || 0 }, { label: "Used MT", value: routeVersion.summary.used_mt || 0 }, { label: "Unused MT", value: routeVersion.summary.unused_mt || 0 }, { label: "Utilization", value: `${routeVersion.summary.fleet_utilization_pct || 0}%` }, { label: "Working Used", value: `${routeVersion.summary.working_time_used_minutes || 0} min` }, { label: "Working Remaining", value: `${routeVersion.summary.working_time_remaining_minutes || 0} min` }]} /></div>
            <div><h4>Multi-Trip</h4><KpiGrid values={[{ label: "Average Trips / MT", value: routeVersion.summary.average_trips_per_mt || 0 }, { label: "Maximum Trips", value: routeVersion.summary.max_trips_per_mt || 0 }, { label: "MT 1 Trip", value: routeVersion.summary.mt_with_1_trip || 0 }, { label: "MT 2 Trips", value: routeVersion.summary.mt_with_2_trips || 0 }, { label: "MT 3+ Trips", value: routeVersion.summary.mt_with_3_plus_trips || 0 }, { label: "Avg Turnaround", value: `${routeVersion.summary.average_turnaround_minutes || 0} min` }]} /></div>
            <div><h4>Bay</h4><KpiGrid values={[{ label: "Average Queue", value: `${routeVersion.summary.average_queue_minutes || 0} min` }, { label: "Maximum Queue", value: `${routeVersion.summary.maximum_queue_minutes || 0} min` }, { label: "Queue Length", value: routeVersion.summary.queue_length || 0 }, { label: "Bay Utilization", value: `${routeVersion.summary.bay_utilization_pct || 0}%` }, { label: "Bay Idle", value: `${routeVersion.summary.bay_idle_minutes || 0} min` }, { label: "Throughput", value: `${routeVersion.summary.loading_throughput_kl_per_hour || 0} KL/h` }, { label: "Bottleneck", value: String(routeVersion.summary.bay_bottleneck || "—") }]} /></div>
            <div><h4>Route</h4><KpiGrid values={[{ label: "Total Distance", value: `${(Number(routeVersion.summary.total_distance_meters || 0) / 1000).toFixed(1)} km` }, { label: "Travel Time", value: `${Math.round(Number(routeVersion.summary.total_travel_time_seconds || 0) / 60)} min` }, { label: "Operating Time", value: `${routeVersion.summary.total_operating_minutes || 0} min` }, { label: "Total Trips", value: routeVersion.summary.total_trips || 0 }, { label: "Average Trip", value: `${routeVersion.summary.average_trip_duration_minutes || 0} min` }]} /></div>
            <div><h4>Cost</h4><KpiGrid values={[{ label: "Total Cost", value: `Rp ${Number(routeVersion.summary.total_cost || 0).toLocaleString("id-ID")}` }, { label: "Cost / KL", value: `Rp ${Number(routeVersion.summary.cost_per_kl || 0).toLocaleString("id-ID")}` }, { label: "Cost / Trip", value: `Rp ${Number(routeVersion.summary.cost_per_trip || 0).toLocaleString("id-ID")}` }, { label: "Cost / MT", value: `Rp ${Number(routeVersion.cost.cost_per_mt || 0).toLocaleString("id-ID")}` }, { label: "Activation Cost", value: `Rp ${Number(routeVersion.summary.activation_cost || 0).toLocaleString("id-ID")}` }, { label: "Distance Cost", value: `Rp ${Number(routeVersion.summary.distance_cost || 0).toLocaleString("id-ID")}` }]} /></div>
            <div><h4>Reoptimization</h4><KpiGrid values={[{ label: "Reroutes", value: routeVersion.summary.reroute_number || 0 }, { label: "LO Reassigned", value: routeVersion.summary.lo_reassigned || 0 }, { label: "Shipment Regrouped", value: routeVersion.summary.shipment_regrouped || 0 }, { label: "MT Changes", value: routeVersion.summary.mt_assignment_changes || 0 }, { label: "Gate-Out Changes", value: routeVersion.summary.gate_out_changes || 0 }, { label: "Plan Stability", value: `${routeVersion.summary.plan_stability_pct || 0}%` }]} /></div>
          </div>
        </Section>}
      </div>}

      {tab === "lo" && <Section title="LO Management" description="Phase 6 fields remain read-only; Phase 7 current assignment and operational status are separate." action={<div className="phase7-action-row"><select value={bulkLOStatus} onChange={(event) => setBulkLOStatus(event.target.value as LoadingOrder["status"])}><option>PLANNED</option><option>ONGOING</option><option>DONE</option></select><button className="phase7-primary" disabled={!selectedLO.size || busy} onClick={() => void applyLOUpdate()}>Apply Operational Update ({selectedLO.size})</button></div>}>
        {!loadingOrders.length ? <EmptyState title="No LO loaded" description="Open Job Overview and load a Phase 6 Prediction Run." /> : <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th><input type="checkbox" checked={selectedLO.size === loadingOrders.length} onChange={(event) => setSelectedLO(event.target.checked ? new Set(loadingOrders.map((row) => row.loading_order_id)) : new Set())} /></th><th>LO ID</th><th>SPBU</th><th>Product</th><th>Volume</th><th>Phase 6 Shipment</th><th>Phase 6 MT</th><th>Current MT</th><th>Trip / Compartment</th><th>Planned Gate Out</th><th>Status</th><th>Frozen</th></tr></thead><tbody>{loadingOrders.map((row) => <tr key={row.loading_order_id}><td><input type="checkbox" checked={selectedLO.has(row.loading_order_id)} onChange={(event) => setSelectedLO((current) => { const next = new Set(current); event.target.checked ? next.add(row.loading_order_id) : next.delete(row.loading_order_id); return next; })} /></td><td><strong>{row.loading_order_id}</strong></td><td>{row.spbu_name || row.spbu_id}<small>{row.spbu_id}</small></td><td>{row.product_name || row.product_id || "—"}</td><td>{row.volume_kl} KL</td><td>{row.phase6_shipment || "—"}</td><td>{row.phase6_mt || "—"}</td><td>{row.current_mt || "—"}</td><td>{row.current_trip ? `Trip ${row.current_trip}` : "—"}<small>{row.current_compartment || ""}</small></td><td>{displayDateTime(row.planned_gate_out)}</td><td><span className={badgeClass(row.status)}>{row.status}</span></td><td>{row.frozen ? <span className="phase7-badge is-warning">{row.frozen_reason}</span> : "No"}</td></tr>)}</tbody></table></div>}
      </Section>}

      {tab === "mt" && <Section title="MT Management" description="Effective ETA = user override, otherwise previous system ETA, otherwise initial planned ETA." action={<div className="phase7-action-row"><button className="phase7-secondary" onClick={() => void loadMasterMT()}><Database size={15} /> Load MT from Master Data</button><button className="phase7-primary" disabled={!vehicles.length || busy} onClick={() => void applyVehicleUpdates()}><Save size={15} /> Apply MT Update</button></div>}>
        {!vehicles.length ? <EmptyState title="No MT loaded" description="Load active vehicles for this Job depot from canonical master data." /> : <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>MT / Registration</th><th>Class / Tags</th><th>Capacity</th><th>Compartments</th><th>Planned ETA Depot</th><th>System ETA Depot</th><th>User ETA Override</th><th>Effective ETA</th><th>Status</th><th>Working Time</th></tr></thead><tbody>{vehicles.map((row) => <tr key={row.mt_id}><td><strong>{row.registration || row.mt_id}</strong><small>{row.mt_id}</small></td><td>Class {row.vehicle_class ?? "—"}<small>{row.tags.join(", ") || "No tags"}</small></td><td>{row.capacity_kl} KL</td><td>{row.number_of_compartments}<small>{row.compartments.map((item) => `${item.compartment_id} ${item.capacity_kl} KL`).join(" · ")}</small></td><td><input type="datetime-local" value={vehicleDrafts[row.mt_id]?.planned || ""} onChange={(event) => setVehicleDrafts((current) => ({ ...current, [row.mt_id]: { ...(current[row.mt_id] || { override: "", status: row.operational_status }), planned: event.target.value } }))} /></td><td>{displayDateTime(row.system_eta_depot)}</td><td><input type="datetime-local" value={vehicleDrafts[row.mt_id]?.override || ""} onChange={(event) => setVehicleDrafts((current) => ({ ...current, [row.mt_id]: { ...(current[row.mt_id] || { planned: "", status: row.operational_status }), override: event.target.value } }))} /></td><td><strong>{displayDateTime(row.effective_eta_depot)}</strong></td><td><select value={vehicleDrafts[row.mt_id]?.status || row.operational_status} onChange={(event) => setVehicleDrafts((current) => ({ ...current, [row.mt_id]: { ...(current[row.mt_id] || { planned: "", override: "" }), status: event.target.value } }))}>{["READY", "ON_TRIP", "RETURNING", "QUEUEING", "LOADING", "UNAVAILABLE"].map((status) => <option key={status}>{status}</option>)}</select></td><td>{row.working_time_used} / {row.working_time_limit} min<small>{row.working_time_remaining} min remaining</small></td></tr>)}</tbody></table></div>}
      </Section>}

      {tab === "bay" && <div className="phase7-grid-2">
        <Section title="Bay Configuration" description="Product compatibility is hard. Loading duration is per product per compartment." action={<div className="phase7-action-row"><button className="phase7-secondary" onClick={() => void createDefaultBaySet()}><RefreshCw size={15} /> Default Set</button><button className="phase7-secondary" disabled={!bay} onClick={addBayConfiguration}><Plus size={15} /> Add Bay</button><button className="phase7-primary" disabled={!bay?.configuration.bays.length} onClick={() => void saveBayConfiguration()}><Save size={15} /> Save Configuration</button></div>}>
          {!bay?.configuration.bays.length ? <EmptyState title="No bay configured" description="Create an editable initial set, then review products, hours, arms, and loading mode." /> : <div className="phase7-bay-grid">{bay.configuration.bays.map((row, index) => <div className="phase7-bay-state" key={String(row.master_bay_id || `${row.bay_id}-${index}`)}><label><span>Bay ID</span><input value={String(row.bay_id || "")} onChange={(event) => updateBayConfiguration(index, "bay_id", event.target.value)} /></label><label><span>Bay Name</span><input value={String(row.bay_name || "")} onChange={(event) => updateBayConfiguration(index, "bay_name", event.target.value)} /></label><label><span>Products</span><select multiple disabled={Boolean(row.all_products_allowed)} value={(row.allowed_products as string[]) || []} onChange={(event) => updateBayConfiguration(index, "allowed_products", Array.from(event.target.selectedOptions, (option) => option.value))}>{products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name}</option>)}</select></label><label className="phase7-check"><input type="checkbox" checked={Boolean(row.all_products_allowed)} onChange={(event) => updateBayConfiguration(index, "all_products_allowed", event.target.checked)} /><span>Allow all products</span></label><label><span>Operational Start</span><input type="time" value={String(row.operational_start || "05:00").slice(0, 5)} onChange={(event) => updateBayConfiguration(index, "operational_start", event.target.value)} /></label><label><span>Operational End</span><input type="time" value={String(row.operational_end || "22:00").slice(0, 5)} onChange={(event) => updateBayConfiguration(index, "operational_end", event.target.value)} /></label><label><span>Loading Arms</span><input type="number" min="1" value={Number(row.number_of_loading_arms || 1)} onChange={(event) => updateBayConfiguration(index, "number_of_loading_arms", Number(event.target.value))} /></label><label><span>Loading Mode</span><select value={String(row.loading_mode || "SEQUENTIAL")} onChange={(event) => updateBayConfiguration(index, "loading_mode", event.target.value)}><option>SEQUENTIAL</option><option>PARALLEL</option></select></label><label><span>Status</span><select value={String(row.active_status || "ACTIVE")} onChange={(event) => updateBayConfiguration(index, "active_status", event.target.value)}><option>ACTIVE</option><option>INACTIVE</option></select></label></div>)}</div>}
          {!!bay?.configuration.loading_durations.length && <div className="phase7-duration-list">{bay.configuration.loading_durations.map((row, index) => <label key={String(row.product_id)}><strong>{String(row.product_name || row.product_id)}</strong><input type="number" min="1" value={Number(row.duration_minutes_per_compartment || 1)} onChange={(event) => updateLoadingDuration(index, Number(event.target.value))} /><span>min / compartment</span></label>)}</div>}
        </Section>
        <Section title="Current Actual Bay State" description="Actual occupancy and queue override the previously predicted state on reroute." action={<button className="phase7-primary" disabled={!bay?.configuration.number_of_bays} onClick={() => void saveActualBayState()}><Save size={15} /> Apply Bay State</button>}>
          {!bay?.configuration.number_of_bays ? <EmptyState title="Configuration required" description="Create bay master configuration first." /> : <div className="phase7-bay-grid">{bay.configuration.bays.map((row) => { const existing = bay.states.find((state) => state.master_bay_id === row.master_bay_id); return <div className="phase7-bay-state" key={String(row.master_bay_id)}><strong>{String(row.bay_name)}</strong><label><span>Current Vehicle</span><select value={String(existing?.current_vehicle_id || "")} onChange={(event) => updateBayState(row.master_bay_id, "current_vehicle_id", event.target.value || null)}><option value="">Bay empty</option>{vehicles.map((vehicle) => <option key={vehicle.mt_id} value={vehicle.mt_id}>{vehicle.registration || vehicle.mt_id}</option>)}</select></label><label><span>Current Compartment</span><input value={String(existing?.current_compartment_id || "")} placeholder="C1" onChange={(event) => updateBayState(row.master_bay_id, "current_compartment_id", event.target.value || null)} /></label><label><span>Current Product</span><select value={String(existing?.current_product_id || "")} onChange={(event) => updateBayState(row.master_bay_id, "current_product_id", event.target.value || null)}><option value="">None</option>{products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name}</option>)}</select></label><label><span>Remaining Loading (min)</span><input type="number" min="0" value={Number(existing?.remaining_loading_minutes || 0)} onChange={(event) => updateBayState(row.master_bay_id, "remaining_loading_minutes", Number(event.target.value))} /></label><label><span>Actual Queue Length</span><input type="number" min="0" value={Number(existing?.actual_queue_length || 0)} onChange={(event) => updateBayState(row.master_bay_id, "actual_queue_length", Number(event.target.value))} /></label></div>; })}</div>}
          <div className="phase7-queue-head"><div><strong>Current Physical Queue</strong><span>Rows are reserved in queue order before CP-SAT schedules new loading.</span></div><button className="phase7-secondary" disabled={!bay?.configuration.number_of_bays || !vehicles.length || !products.length} onClick={addBayQueueRow}><Plus size={15} /> Add Queue Row</button></div>
          {!!bay?.queue.length && <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>Bay</th><th>Position</th><th>MT</th><th>Compartment</th><th>Product</th><th>Duration</th><th /></tr></thead><tbody>{bay.queue.map((queueRow, index) => <tr key={`${String(queueRow.master_bay_id)}-${index}`}><td><select value={String(queueRow.master_bay_id || "")} onChange={(event) => updateBayQueueRow(index, "master_bay_id", event.target.value)}>{bay.configuration.bays.map((bayRow) => <option key={String(bayRow.master_bay_id)} value={String(bayRow.master_bay_id)}>{String(bayRow.bay_name)}</option>)}</select></td><td><input type="number" min="1" value={Number(queueRow.queue_position || 1)} onChange={(event) => updateBayQueueRow(index, "queue_position", Number(event.target.value))} /></td><td><select value={String(queueRow.vehicle_id || "")} onChange={(event) => updateBayQueueRow(index, "vehicle_id", event.target.value)}>{vehicles.map((vehicle) => <option key={vehicle.mt_id} value={vehicle.mt_id}>{vehicle.registration || vehicle.mt_id}</option>)}</select></td><td><input value={String(queueRow.compartment_id || "")} onChange={(event) => updateBayQueueRow(index, "compartment_id", event.target.value || null)} /></td><td><select value={String(queueRow.product_id || "")} onChange={(event) => updateBayQueueRow(index, "product_id", event.target.value)}>{products.map((product) => <option key={product.product_id} value={product.product_id}>{product.product_name}</option>)}</select></td><td><input type="number" min="1" value={Number(queueRow.estimated_loading_duration_minutes || 8)} onChange={(event) => updateBayQueueRow(index, "estimated_loading_duration_minutes", Number(event.target.value))} /></td><td><button className="phase7-link is-danger" onClick={() => setBay((current) => current ? { ...current, queue: current.queue.filter((_, rowIndex) => rowIndex !== index) } : current)}>Remove</button></td></tr>)}</tbody></table></div>}
          <div className="phase7-note"><Clock3 size={15} /> Current queue rows: {bay?.queue.length || 0}. Apply Bay State to persist this actual operational snapshot.</div>
        </Section>
      </div>}

      {tab === "parameter" && <div className="phase7-grid-2">
        <Section title="Optimization Parameter Profile" description="Load, review, save a new version, or Save As. Every solver run copies an immutable effective snapshot." action={<div className="phase7-action-row"><button className="phase7-secondary" onClick={() => void saveProfile(false)}><Save size={15} /> Save</button><button className="phase7-secondary" onClick={() => void saveProfile(true)}>Save As</button></div>}>
          <label className="phase7-field"><span>Profile</span><select value={selectedProfile} onChange={(event) => setSelectedProfile(event.target.value)}>{profiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_name} · v{profile.version}{profile.is_default ? " · Default" : ""}</option>)}</select></label>
          <p className="phase7-profile-description">{selectedProfileRow?.description}</p>
          <div className="phase7-form-grid">
            <label><span>Objective</span><select value={String(parameterDraft.objective || "MIN_TOTAL_COST")} onChange={(event) => setParameterDraft((current) => ({ ...current, objective: event.target.value }))}><option>MIN_TOTAL_COST</option><option>MIN_TOTAL_DISTANCE</option><option>MIN_TOTAL_OPERATING_TIME</option></select></label>
            {[ ["freeze_window_minutes", "Freeze Window (min)", 60], ["reoptimization_interval_minutes", "Reoptimization Interval", 60], ["optimization_time_limit", "Optimization Time Limit", 30], ["max_coordination_iterations", "Coordination Iterations", 5], ["departure_time_tolerance_minutes", "Departure Tolerance", 5], ["return_time_tolerance_minutes", "Return Tolerance", 5], ["maximum_trips_per_mt", "Maximum Trips / MT", 6], ["default_vehicle_working_time_minutes", "Default Working Time", 720], ["default_spbu_service_minutes", "SPBU Service Time", 30], ["gate_process_time", "Gate Process Time", 5] ].map(([key, label, fallback]) => <label key={String(key)}><span>{label}</span><input type="number" value={profileNumber(String(key), Number(fallback))} onChange={(event) => setParameterDraft((current) => ({ ...current, [String(key)]: Number(event.target.value) }))} /></label>)}
            <label><span>Route Vehicle Mode</span><select value={String(parameterDraft.route_vehicle_mode || "GENERAL_VEHICLE")} onChange={(event) => setParameterDraft((current) => ({ ...current, route_vehicle_mode: event.target.value }))}><option>GENERAL_VEHICLE</option><option>TRUCK</option></select></label>
            <label><span>Loading Mode</span><select value={String(parameterDraft.loading_mode || "SEQUENTIAL")} onChange={(event) => setParameterDraft((current) => ({ ...current, loading_mode: event.target.value }))}><option>SEQUENTIAL</option><option>PARALLEL</option></select></label>
            <label className="phase7-check"><input type="checkbox" checked={Boolean(parameterDraft.traffic_aware ?? true)} onChange={(event) => setParameterDraft((current) => ({ ...current, traffic_aware: event.target.checked }))} /><span>Traffic aware</span></label>
            <label className="phase7-check"><input type="checkbox" checked={Boolean(parameterDraft.route_matrix_cache_enabled ?? true)} onChange={(event) => setParameterDraft((current) => ({ ...current, route_matrix_cache_enabled: event.target.checked }))} /><span>Route matrix cache</span></label>
            <label><span>Cache TTL (min)</span><input type="number" value={profileNumber("route_matrix_cache_ttl_minutes", 60)} onChange={(event) => setParameterDraft((current) => ({ ...current, route_matrix_cache_ttl_minutes: Number(event.target.value) }))} /></label>
          </div>
          <div className="phase7-note"><MapPinned size={15} /> Google API key is managed in Google Maps Integration. GENERAL_VEHICLE uses DRIVE road data when configured; explicit TRUCK currently records a visibly labelled master-Haversine fallback because the configured client does not claim truck routing support.</div>
        </Section>
        <Section title="Cost & Penalty Controls" description="Objective changes never weaken hard constraints. Phase 6 and history remain configurable soft preferences.">
          <div className="phase7-form-grid">{[["cost_per_km", "Cost / KM"], ["cost_per_operating_hour", "Operating / Hour"], ["queue_cost", "Queue / Minute"], ["loading_cost", "Loading / Minute"], ["overtime_cost", "Overtime / Minute"], ["unserved_penalty", "Unserved Penalty"], ["late_penalty", "Late Penalty"], ["overtime_penalty", "Overtime Penalty"], ["phase6_vehicle_change_penalty", "Phase 6 Vehicle Change"], ["phase6_shipment_change_penalty", "Phase 6 Shipment Change"], ["historical_pairing_penalty", "Historical Pairing"], ["historical_mt_affinity_penalty", "Historical MT Affinity"], ["plan_change_penalty", "General Plan Change"], ["vehicle_reassignment_penalty", "Vehicle Reassignment"], ["shipment_change_penalty", "Shipment Change"], ["route_sequence_change_penalty", "Route Sequence Change"], ["gateout_change_penalty", "Gate-Out Change"], ["bay_queue_penalty", "Bay Queue Penalty"], ["bay_change_penalty", "Bay Change Penalty"]].map(([key, label]) => <label key={key}><span>{label}</span><input type="number" value={profileNumber(key)} onChange={(event) => setParameterDraft((current) => ({ ...current, [key]: Number(event.target.value) }))} /></label>)}</div>
          <div className="phase7-note"><GitCompareArrows size={15} /> Original Phase 6 shipment/MT values are never overwritten by these penalties or by Phase 7 assignments.</div>
        </Section>
        <div className="phase7-grid-span"><Section title="Vehicle Activation Cost Rules" description="Priority and tag specificity determine the matching activation cost for each used MT.">
          <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>Vehicle Class</th><th>Vehicle Tag</th><th>Activation Cost</th><th>Priority</th><th /></tr></thead><tbody>{(Array.isArray(parameterDraft.vehicle_activation_cost_rules) ? parameterDraft.vehicle_activation_cost_rules as Array<Record<string, unknown>> : []).map((rule, index) => <tr key={index}><td><input type="number" value={Number(rule.vehicle_class || 0)} onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, vehicle_class: Number(event.target.value) } : row) }))} /></td><td><input value={String(rule.vehicle_tag || "")} placeholder="Optional" onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, vehicle_tag: event.target.value || null } : row) }))} /></td><td><input type="number" value={Number(rule.activation_cost || 0)} onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, activation_cost: Number(event.target.value) } : row) }))} /></td><td><input type="number" value={Number(rule.priority || 0)} onChange={(event) => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).map((row, rowIndex) => rowIndex === index ? { ...row, priority: Number(event.target.value) } : row) }))} /></td><td><button className="phase7-link is-danger" onClick={() => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: (current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []).filter((_, rowIndex) => rowIndex !== index) }))}>Remove</button></td></tr>)}</tbody></table></div>
          <button className="phase7-secondary" onClick={() => setParameterDraft((current) => ({ ...current, vehicle_activation_cost_rules: [...(current.vehicle_activation_cost_rules as Array<Record<string, unknown>> || []), { vehicle_class: 8, vehicle_tag: null, activation_cost: 500000, priority: 10 }] }))}><Plus size={15} /> Add Activation Rule</button>
        </Section></div>
      </div>}

      {tab === "route" && <div className="phase7-route-page"><Section title="Route Plan" description="Version-aware MT → Trip → SPBU sequence with compartment-level LO assignment." action={<div className="phase7-action-row"><select value={selectedVersion} onChange={(event) => void selectRouteVersion(event.target.value)}>{versions.map((version) => <option key={String(version.route_version_id)} value={String(version.route_version_id)}>{String(version.version_label)} · {String(version.reason)}</option>)}</select><select value={selectedMT} onChange={(event) => { setSelectedMT(event.target.value); setSelectedTrip("ALL"); }}><option value="">All MT</option>{routeVehicleOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><select value={selectedTrip} onChange={(event) => setSelectedTrip(event.target.value === "ALL" ? "ALL" : Number(event.target.value))}><option value="ALL">All Trips</option>{Array.from(new Set((routeVersion?.trips || []).filter((row) => !selectedMT || row.vehicle_id === selectedMT).map((row) => row.trip_number))).map((number) => <option key={number} value={number}>Trip {number}</option>)}</select></div>}>
        {!routeVersion ? <EmptyState title="No route version" description="Run the initial optimization to create V1." /> : <>
          <div className="phase7-route-filters"><label><span>LO</span><input value={routeLOFilter} placeholder="Filter LO ID" onChange={(event) => setRouteLOFilter(event.target.value)} /></label><label><span>SPBU</span><input value={routeSPBUFilter} placeholder="Code or name" onChange={(event) => setRouteSPBUFilter(event.target.value)} /></label><label><span>Product</span><input value={routeProductFilter} placeholder="Product ID" onChange={(event) => setRouteProductFilter(event.target.value)} /></label></div>
          <KpiGrid values={[{ label: "Solver Status", value: routeVersion.solver_status }, { label: "First Gate Out", value: displayDateTime(routeVersion.first_gate_out) }, { label: "Last Gate Out", value: displayDateTime(routeVersion.last_gate_out) }, { label: "Dispatch Span", value: `${routeVersion.depot_dispatch_span_minutes} min` }]} />
          {!filteredTrips.length ? <EmptyState title="No matching route" description="Adjust the MT, Trip, LO, SPBU, or Product filters." /> : <div className="phase7-trip-stack">{filteredTrips.map((trip) => <div className="phase7-trip-card" key={trip.route_version_trip_id}>
            <div className="phase7-trip-head"><div><Truck size={18} /><strong>{trip.registration || trip.vehicle_id}</strong><span>Trip {trip.trip_number}</span><span>{trip.shipment_id}</span></div><span className={badgeClass(trip.assignment_status)}>{trip.assignment_status}</span></div>
            <div className="phase7-timeline"><span><small>READY</small>{displayDateTime(trip.vehicle_ready_at_depot)}</span><i /><span><small>QUEUE</small>{displayDateTime(trip.queue_start)}<small>{trip.queue_minutes} min</small></span><i /><span><small>LOADING</small>{displayDateTime(trip.loading_start)}<small>finish {displayDateTime(trip.loading_finish)}</small></span><i /><span><small>GATE OUT</small>{displayDateTime(trip.gate_out)}</span><i /><span><small>RETURN</small>{displayDateTime(trip.return_depot)}</span></div>
            <div className="phase7-table-wrap"><table className="phase7-table is-dense"><thead><tr><th>MT</th><th>Trip</th><th>Gate Out</th><th>LO</th><th>SPBU</th><th>Product</th><th>Volume</th><th>Sequence</th><th>ETA</th><th>ETD</th><th>Return Depot</th><th>Distance</th><th>Travel Time</th><th>Compartment</th><th>Frozen</th></tr></thead><tbody>{trip.loading_orders.filter(loMatchesRouteFilters).sort((a, b) => a.stop_sequence - b.stop_sequence).map((row) => { const stop = trip.stops.find((item) => item.sequence === row.stop_sequence); return <tr key={row.loading_order_id}><td>{trip.registration || trip.vehicle_id}</td><td>{trip.trip_number}</td><td>{displayDateTime(trip.gate_out)}</td><td>{row.loading_order_id}</td><td>{row.spbu_name || row.spbu_id}</td><td>{row.product_id || "—"}</td><td>{row.volume_kl} KL</td><td>{row.stop_sequence}</td><td>{displayDateTime(stop?.arrival_time || row.eta)}</td><td>{displayDateTime(stop?.departure_time)}</td><td>{displayDateTime(trip.return_depot)}</td><td>{stop ? `${(stop.distance_from_previous_meters / 1000).toFixed(1)} km` : "—"}</td><td>{stop ? `${Math.round(stop.travel_from_previous_seconds / 60)} min` : "—"}</td><td>{row.compartment_id}</td><td>{row.frozen ? "Yes" : "No"}</td></tr>; })}</tbody></table></div>
            <div className="phase7-trip-metrics"><span>{(trip.distance_meters / 1000).toFixed(1)} km</span><span>{Math.round(trip.travel_time_seconds / 60)} min driving</span><span>{trip.operating_minutes} min operating</span></div>
          </div>)}</div>}
        </>}
      </Section>
      {routeVersion && <Section title="Vehicle Multi-Trip Timeline" description="One continuous operational timeline per MT for the selected immutable route version.">
        <div className="phase7-vehicle-timeline">{tripsByMT.map(([vehicleId, row]) => <div key={vehicleId}><div className="phase7-vehicle-label"><Truck size={17} /><strong>{row.registration}</strong><small>{vehicleId}</small></div><div className="phase7-vehicle-events">{row.trips.map((trip) => <div key={trip.route_version_trip_id}><strong>Trip {trip.trip_number}</strong><span>Ready {displayDateTime(trip.vehicle_ready_at_depot)}</span><span>Queue {displayDateTime(trip.queue_start)}–{displayDateTime(trip.loading_start)}</span><span>Load {displayDateTime(trip.loading_start)}–{displayDateTime(trip.loading_finish)}</span><span>Gate Out {displayDateTime(trip.gate_out)}</span><span>Trip {displayDateTime(trip.gate_out)}–{displayDateTime(trip.return_depot)}</span><span>Return {displayDateTime(trip.return_depot)}</span></div>)}</div></div>)}</div>
      </Section>}
      </div>}

      {tab === "simulation" && <div className="phase7-grid-2">
        <Section title="Gate-Out KL per Hour" description="True hourly buckets, including zero-volume gaps, plus cumulative gate-out volume for the selected route version.">{routeVersion ? <ReactECharts style={{ height: 320 }} option={{ tooltip: { trigger: "axis" }, legend: { data: ["Hourly KL", "Cumulative KL"] }, xAxis: { type: "category", data: hourlySimulation.map((row) => row.label) }, yAxis: [{ type: "value", name: "KL" }], series: [{ name: "Hourly KL", type: "bar", data: hourlySimulation.map((row) => row.gateOutKL), itemStyle: { color: "#0b73bf" } }, { name: "Cumulative KL", type: "line", data: hourlySimulation.map((row) => row.cumulativeKL), smooth: true, itemStyle: { color: "#8aaa18" } }] }} /> : <EmptyState title="No simulation" description="Select or create a route version." />}</Section>
        <Section title="MT ETA Depot & Returning Capacity" description="Number of returning MT and total returning KL capacity aggregated per hour.">{routeVersion ? <ReactECharts style={{ height: 320 }} option={{ tooltip: { trigger: "axis" }, legend: { data: ["Returning MT", "Returning Capacity"] }, xAxis: { type: "category", data: hourlySimulation.map((row) => row.label) }, yAxis: [{ type: "value", name: "MT", minInterval: 1 }, { type: "value", name: "KL" }], series: [{ name: "Returning MT", type: "bar", data: hourlySimulation.map((row) => row.returningMT), itemStyle: { color: "#ea4a43" } }, { name: "Returning Capacity", type: "line", yAxisIndex: 1, data: hourlySimulation.map((row) => row.returningCapacityKL), itemStyle: { color: "#b8d211" } }] }} /> : <EmptyState title="No return projection" description="Select or create a route version." />}</Section>
        {routeVersion && <div className="phase7-grid-span"><Section title="Simulation KPI" description="Version-aware depot, fleet, and multi-trip performance."><KpiGrid values={[{ label: "First Gate Out", value: displayDateTime(routeVersion.first_gate_out) }, { label: "Last Gate Out", value: displayDateTime(routeVersion.last_gate_out) }, { label: "Depot Dispatch Span", value: `${routeVersion.depot_dispatch_span_minutes} min` }, { label: "Max Trips / MT", value: routeVersion.summary.max_trips_per_mt || 0 }, { label: "Fleet Utilization", value: `${routeVersion.summary.fleet_utilization_pct || 0}%` }, { label: "Average Turnaround", value: `${routeVersion.summary.average_turnaround_minutes || routeVersion.summary.average_trip_duration_minutes || 0} min` }]} /></Section></div>}
      </div>}

      {tab === "map" && <Section title="Geographic Route Map" description="OR-Tools selects the plan. Cached Google Routes data supplies road distance/time/geometry when available; fallback geometry is visibly labelled." action={<div className="phase7-action-row"><select value={selectedVersion} onChange={(event) => void selectRouteVersion(event.target.value)}>{versions.map((version) => <option key={String(version.route_version_id)} value={String(version.route_version_id)}>{String(version.version_label)} · {String(version.reason)}</option>)}</select><select value={selectedMT} onChange={(event) => { setSelectedMT(event.target.value); setSelectedTrip("ALL"); }}><option value="">All MT</option>{routeVehicleOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><select value={selectedTrip} onChange={(event) => setSelectedTrip(event.target.value === "ALL" ? "ALL" : Number(event.target.value))}><option value="ALL">All Trips for selected MT</option>{Array.from(new Set((routeVersion?.trips || []).filter((row) => !selectedMT || row.vehicle_id === selectedMT).map((row) => row.trip_number))).map((number) => <option key={number} value={number}>Trip {number}</option>)}</select></div>}>
        {!selectedTrips.some((trip) => trip.route_geometry.length) ? <EmptyState title="No mappable geometry" description="Canonical coordinates or Google route geometry are missing for this selection." /> : <div className="phase7-map-grid">
          <MapContainer center={selectedTrips.find((trip) => trip.route_geometry.length)?.route_geometry[0] ? [selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].latitude, selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].longitude] : [-6.2, 106.8]} zoom={8} scrollWheelZoom className="phase7-map">
            <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
            {selectedTrips.find((trip) => trip.route_geometry.length)?.route_geometry[0] && <CircleMarker center={[selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].latitude, selectedTrips.find((trip) => trip.route_geometry.length)!.route_geometry[0].longitude]} radius={9} pathOptions={{ color: "#15385b", fillColor: "#fff", fillOpacity: 1, weight: 3 }}><Popup><strong>Depot</strong><br />Start and return point for the selected routes.</Popup></CircleMarker>}
            {selectedTrips.map((trip, index) => { const positions = trip.route_geometry.map((point) => [point.latitude, point.longitude] as [number, number]); const color = ["#0b73bf", "#ea4a43", "#8aaa18", "#7c3aed"][index % 4]; return <span key={trip.route_version_trip_id}>{positions.length > 1 && <Polyline positions={positions} pathOptions={{ color, weight: 4, dashArray: trip.route_geometry_source === "GOOGLE_ROUTES_GEOJSON" ? undefined : "8 8" }} />}{trip.stops.filter((stop) => stop.latitude !== null && stop.longitude !== null).map((stop) => <CircleMarker key={`${trip.route_version_trip_id}-${stop.sequence}`} center={[stop.latitude!, stop.longitude!]} radius={7} pathOptions={{ color, fillColor: color, fillOpacity: 0.9 }}><Popup><strong>{trip.registration} · Trip {trip.trip_number}</strong><br />Stop {stop.sequence} · {stop.spbu_name}<br />LO {stop.loading_order_ids.join(", ")}<br />Product {stop.products.filter(Boolean).join(", ") || "—"}<br />Volume {stop.volume_kl} KL<br />ETA {displayDateTime(stop.arrival_time)}<br />Distance {(stop.distance_from_previous_meters / 1000).toFixed(1)} km<br />Travel {Math.round(stop.travel_from_previous_seconds / 60)} min</Popup></CircleMarker>)}</span>; })}
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
        {!versions.length ? <EmptyState title="No route history" description="The first optimization creates immutable V1." /> : <div className="phase7-version-grid">{versions.map((version) => <button key={String(version.route_version_id)} className={selectedVersion === version.route_version_id ? "is-active" : ""} onClick={() => void selectRouteVersion(String(version.route_version_id))}><span>{String(version.version_label)}</span><strong>{String(version.reason)}</strong><small>{displayDateTime(String(version.created_at))}</small><i className={badgeClass(String(version.solver_status))}>{String(version.solver_status)}</i></button>)}</div>}
        {routeVersion && <div className="phase7-audit"><div><h4>{routeVersion.version_label} audit inputs</h4><span>Parameter checksum: <code>{routeVersion.parameter_checksum}</code></span></div>{routeVersion.audit_events.length ? routeVersion.audit_events.map((event, index) => <div className="phase7-audit-event" key={index}><History size={15} /><pre>{JSON.stringify(event, null, 2)}</pre></div>) : <div className="phase7-note">Baseline or no actual operational changes recorded before this version.</div>}<KpiGrid values={[{ label: "Plan Adherence", value: `${routeVersion.comparison.plan_adherence_pct ?? 100}%` }, { label: "Vehicle Changes", value: Number(routeVersion.comparison.vehicle_assignment_changes || 0) }, { label: "Shipment Changes", value: Number(routeVersion.comparison.shipment_changes || 0) }, { label: "Gate-Out Variance", value: `${routeVersion.comparison.gate_out_variance_minutes || 0} min` }]} /></div>}
      </Section>}

      {busy && <div className="phase7-busy"><LoaderCircle className="animate-spin" size={28} /><strong>Phase 7 is calculating…</strong><span>Duplicate optimization submission is disabled.</span></div>}
    </div>
  );
}
