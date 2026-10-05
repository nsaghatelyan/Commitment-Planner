import type {
  AnalysisRun,
  Commitment,
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
  setStatus: (recId: string, status: Recommendation["status"]) =>
    request<{ id: string; status: string }>(`/recommendations/${recId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
};
