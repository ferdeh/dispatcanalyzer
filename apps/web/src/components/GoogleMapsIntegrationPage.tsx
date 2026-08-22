import { KeyRound, Save, TestTube2, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { apiGet, apiSend } from "../lib/api";

type Settings = {
  api_key_configured: boolean;
  masked_api_key: string | null;
  encryption_ready: boolean;
  connection_status: string;
  routing_mode: string;
  routing_preference: string;
  cache_ttl_minutes: number;
  departure_time_bucket_minutes: number;
  default_depot_processing_minutes: number;
  default_spbu_service_minutes: number;
  default_return_processing_minutes: number;
  default_turnaround_buffer_minutes: number;
  default_route_duration_minutes: number;
  last_test_result: { checks?: Record<string, string>; error_code?: string };
};

function Badge({ value }: { value: string }) {
  const good = ["CONNECTED", "AVAILABLE", "PASS", "COMPLETE", "DRIVE ONLY"].includes(value);
  const warning = ["UNKNOWN", "NOT_TESTED", "NOT_AVAILABLE", "INCOMPLETE", "NOT_RUN"].includes(value);
  return <span className={`inline-flex border px-2 py-1 text-[11px] font-semibold uppercase ${good ? "border-mint bg-mint/10 text-mint" : warning ? "border-amber bg-amber/10 text-amber" : "border-rust bg-rust/10 text-rust"}`}>{value.replace(/_/g, " ")}</span>;
}

const numericFields: Array<[keyof Settings, string]> = [
  ["cache_ttl_minutes", "Cache TTL (minutes)"],
  ["departure_time_bucket_minutes", "Departure Bucket (minutes)"],
  ["default_depot_processing_minutes", "Depot Processing (minutes)"],
  ["default_spbu_service_minutes", "SPBU Service / Stop (minutes)"],
  ["default_return_processing_minutes", "Return Processing (minutes)"],
  ["default_turnaround_buffer_minutes", "Turnaround Buffer (minutes)"],
  ["default_route_duration_minutes", "Default One-Leg Duration (minutes)"],
];

export function GoogleMapsIntegrationPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setSettings(await apiGet<Settings>("/api/v1/settings/google-routes"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Google Routes settings could not be loaded.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  function update<K extends keyof Settings>(field: K, value: Settings[K]) {
    setSettings((current) => current ? { ...current, [field]: value } : current);
  }

  async function save() {
    if (!settings) return;
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      const payload = Object.fromEntries(numericFields.map(([field]) => [field, settings[field]]));
      const next = await apiSend<Settings>("/api/v1/settings/google-routes", "PUT", {
        ...payload,
        routing_preference: settings.routing_preference,
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setSettings(next);
      setApiKey("");
      setNotice("Google Maps integration settings saved. The browser no longer retains the submitted key.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Settings could not be saved.");
    } finally {
      setLoading(false);
    }
  }

  async function testConnection() {
    setLoading(true);
    setError(null);
    setNotice(null);
    try {
      await apiSend("/api/v1/settings/google-routes/test", "POST");
      await load();
      setNotice("Connection test completed.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Connection test failed.");
    } finally {
      setLoading(false);
    }
  }

  async function deleteKey() {
    if (!window.confirm("Delete the stored Google Maps API key?")) return;
    setLoading(true);
    try {
      setSettings(await apiSend<Settings>("/api/v1/settings/google-routes/api-key", "DELETE"));
      setApiKey("");
      setNotice("Stored API key deleted.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "API key could not be deleted.");
    } finally {
      setLoading(false);
    }
  }

  if (!settings) return <div className="border border-line bg-white p-8 text-sm text-slate-500">{loading ? "Loading Google Maps Integration…" : error ?? "Settings unavailable."}</div>;
  const checks = settings.last_test_result?.checks ?? {};
  return (
    <div className="space-y-5">
      {error && <div className="flex items-start justify-between border border-rust bg-rust/5 px-4 py-3 text-sm text-rust"><span>{error}</span><button onClick={() => setError(null)}><XCircle size={17} /></button></div>}
      {notice && <div className="border border-mint bg-mint/5 px-4 py-3 text-sm text-mint">{notice}</div>}

      <section className="border border-line bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-sm font-semibold uppercase tracking-wide text-slate-600">Settings · Google Maps Integration</div><p className="mt-1 text-xs text-slate-500">The API key is encrypted at rest and used only by the backend. It is never returned in full or stored in browser storage.</p></div><div className="flex gap-2"><Badge value={settings.connection_status} /><Badge value="DRIVE ONLY" /></div></div>
        {!settings.encryption_ready && <div className="mt-4 border border-rust bg-rust/5 p-3 text-sm text-rust">Server encryption is not configured. Set GOOGLE_ROUTES_ENCRYPTION_KEY before saving an API key.</div>}
      </section>

      <section className="grid gap-5 xl:grid-cols-2">
        <div className="border border-line bg-white p-5">
          <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-slate-600"><KeyRound size={16} /> API Configuration</div>
          <label className="mt-4 grid gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Google Maps API Key<input type="password" autoComplete="new-password" className="border border-line px-3 py-2 text-sm font-normal normal-case tracking-normal" placeholder={settings.masked_api_key ?? "Paste a new key"} value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
          <div className="mt-2 text-xs text-slate-500">Stored key: {settings.masked_api_key ?? "Not configured"}</div>
          <div className="mt-4 flex flex-wrap gap-2"><button className="inline-flex items-center gap-2 bg-petroblue px-3 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={loading || (!settings.api_key_configured && !apiKey.trim())} onClick={() => void save()}><Save size={15} /> {settings.api_key_configured && apiKey ? "Replace Key" : "Save"}</button><button className="inline-flex items-center gap-2 border border-line px-3 py-2 text-sm disabled:opacity-40" disabled={loading || !settings.api_key_configured} onClick={() => void testConnection()}><TestTube2 size={15} /> Test Connection</button><button className="inline-flex items-center gap-2 border border-rust px-3 py-2 text-sm text-rust disabled:opacity-40" disabled={loading || !settings.api_key_configured} onClick={() => void deleteKey()}><Trash2 size={15} /> Delete Key</button></div>
          {Object.keys(checks).length > 0 && <div className="mt-5 space-y-2">{Object.entries(checks).map(([name, status]) => <div key={name} className="flex items-center justify-between border-b border-line pb-2 text-sm"><span>{name.replace(/_/g, " ")}</span><Badge value={status} /></div>)}</div>}
        </div>

        <div className="border border-line bg-white p-5">
          <div className="text-sm font-semibold uppercase tracking-wide text-slate-600">Route Configuration</div>
          <div className="mt-4 border border-petroblue bg-petroblue/5 p-3 text-sm text-petroblue"><strong>DRIVE only for Indonesia.</strong> Phase 6 does not send TRUCK or Large Vehicle Routing requests.</div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <div className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Vehicle Routing Mode<div className="border border-line bg-slate-50 px-3 py-2 text-sm font-normal normal-case text-slate-700">{settings.routing_mode}</div></div>
            <label className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Traffic Preference<select className="border border-line bg-white px-3 py-2 text-sm font-normal normal-case" value={settings.routing_preference} onChange={(event) => update("routing_preference", event.target.value)}><option>TRAFFIC_UNAWARE</option><option>TRAFFIC_AWARE</option><option>TRAFFIC_AWARE_OPTIMAL</option></select></label>
            {numericFields.map(([field, label]) => <label key={String(field)} className="grid gap-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{label}<input className="border border-line px-3 py-2 text-sm font-normal normal-case" type="number" min="0" value={Number(settings[field])} onChange={(event) => update(field, Number(event.target.value) as never)} /></label>)}
          </div>
          <button className="mt-5 inline-flex items-center gap-2 bg-petroblue px-4 py-2 text-sm font-semibold text-white disabled:opacity-40" disabled={loading} onClick={() => void save()}><Save size={15} /> Save Route Configuration</button>
        </div>
      </section>

    </div>
  );
}
