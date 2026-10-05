import type {
  AnalysisRun,
  CollectionRunInfo,
  Commitment,
  Connection,
  ConnectionInput,
  ConnectionTestResult,
  DailyUsage,
  Recommendation,
  RiskProfile,
  TenantSummary,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  tenants: () => request<TenantSummary[]>("/tenants"),
  latestRun: (tenantId: string, risk: RiskProfile) =>
    request<AnalysisRun>(`/tenants/${tenantId}/analysis-runs/latest?risk_profile=${risk}`),
  runAnalysis: (tenantId: string, risk: RiskProfile) =>
    request<AnalysisRun>(`/tenants/${tenantId}/analysis-runs`, {
      method: "POST",
      body: JSON.stringify({ risk_profile: risk, wait: true }),
    }),
  recommendations: (tenantId: string, runId: string) =>
    request<Recommendation[]>(`/tenants/${tenantId}/analysis-runs/${runId}/recommendations`),
  dailyUsage: (tenantId: string, days = 90) =>
    request<DailyUsage>(`/tenants/${tenantId}/usage/daily?days=${days}`),
  commitments: (tenantId: string) => request<Commitment[]>(`/tenants/${tenantId}/commitments`),
  createTenant: (name: string, risk_profile: RiskProfile) =>
    request<{ id: string; name: string; slug: string }>("/tenants", {
      method: "POST",
      body: JSON.stringify({ name, risk_profile }),
    }),
  connections: (tenantId: string) => request<Connection[]>(`/tenants/${tenantId}/connections`),
  createConnection: (tenantId: string, body: ConnectionInput) =>
    request<Connection>(`/tenants/${tenantId}/connections`, { method: "POST", body: JSON.stringify(body) }),
  updateConnection: (id: string, body: Partial<ConnectionInput>) =>
    request<Connection>(`/connections/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteConnection: async (id: string) => {
    const res = await fetch(`/api/connections/${id}`, { method: "DELETE" });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
  },
  testConnection: (id: string) => request<ConnectionTestResult>(`/connections/${id}/test`, { method: "POST" }),
  collect: (id: string, analyze = true) =>
    request<CollectionRunInfo>(`/connections/${id}/collect`, { method: "POST", body: JSON.stringify({ analyze }) }),
  setStatus: (recId: string, status: Recommendation["status"]) =>
    request<{ id: string; status: string }>(`/recommendations/${recId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
};
