let apiUrl = null;

export async function loadConfig() {
  const res = await fetch("/config.json", { cache: "no-store" });
  if (!res.ok) throw new Error("Could not load config.json. Run the deploy script to create it.");
  const cfg = await res.json();
  apiUrl = cfg.apiUrl.replace(/\/$/, "");
}

const WS_KEY = "lotwise.workspace";

export function workspaceId() {
  let id = null;
  try {
    id = localStorage.getItem(WS_KEY);
  } catch {
    /* storage blocked: fall through to a session-only id */
  }
  if (!id) {
    id = crypto.randomUUID();
    try {
      localStorage.setItem(WS_KEY, id);
    } catch {
      /* ignore */
    }
  }
  return id;
}

export function resetWorkspaceId() {
  try {
    localStorage.removeItem(WS_KEY);
  } catch {
    /* ignore */
  }
}

async function call(method, path, { body, query } = {}) {
  const q = new URLSearchParams({ workspaceId: workspaceId(), ...(query || {}) });
  const url = `${apiUrl}${path}${method === "GET" || method === "DELETE" ? `?${q}` : ""}`;
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify({ workspaceId: workspaceId(), ...body }) : undefined,
    });
  } catch {
    throw new Error("Can't reach the Lotwise server. Check your connection and try again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

export const api = {
  createImport: (filename, content) => call("POST", "/imports", { body: { filename, content } }),
  preview: (id, mapping) => call("POST", `/imports/${id}/preview`, { body: { mapping } }),
  confirm: (id, mapping) => call("POST", `/imports/${id}/confirm`, { body: { mapping } }),
  listImports: () => call("GET", "/imports"),
  deleteImport: (id) => call("DELETE", `/imports/${id}`),
  report: (fy) => call("GET", "/report", { query: fy ? { fy } : {} }),
  clearWorkspace: () => call("DELETE", "/workspace"),
};

export async function fileToCsvText(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
    const XLSX = await import("xlsx");
    const wb = XLSX.read(await file.arrayBuffer(), { type: "array", cellDates: false });
    const sheet = wb.Sheets[wb.SheetNames[0]];
    return XLSX.utils.sheet_to_csv(sheet, { rawNumbers: false });
  }
  if (name.endsWith(".pdf")) {
    throw new Error(`${file.name} is a PDF. Export a CSV or Excel tradebook from your broker instead.`);
  }
  return file.text();
}
