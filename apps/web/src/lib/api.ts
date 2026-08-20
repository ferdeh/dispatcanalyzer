const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type SeriesPoint = { name: string; value: number };

async function responseError(response: Response): Promise<Error> {
  try {
    const payload = await response.json() as { detail?: string | { message?: string; code?: string } };
    if (typeof payload.detail === "string") return new Error(payload.detail);
    if (payload.detail?.message) return new Error(payload.detail.message);
  } catch {
    // Non-JSON failures fall through to the stable HTTP status message.
  }
  return new Error(`${response.status} ${response.statusText}`);
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function importSampleData(): Promise<{ status: string; imports: Record<string, string> }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/imports/sample`, { method: "POST" });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function uploadImportFile(domain: string, sheetName: string, file: File): Promise<{ import_id: string; domain: string }> {
  const params = new URLSearchParams({ domain, sheet_name: sheetName });
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${API_BASE_URL}/api/v1/imports?${params.toString()}`, {
    method: "POST",
    body
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function downloadFromApi(path: string, fallbackFilename: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? fallbackFilename;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function apiSend<T>(path: string, method: "POST" | "PUT" | "PATCH" | "DELETE", body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function apiForm<T>(path: string, body: FormData): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "POST", body });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

export async function downloadFormFromApi(path: string, body: FormData, fallbackFilename: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method: "POST", body });
  if (!response.ok) {
    throw await responseError(response);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match?.[1] ?? fallbackFilename;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
