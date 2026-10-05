export type RiskProfile = "conservative" | "balanced" | "aggressive";
export const RISK_PROFILES: RiskProfile[] = ["conservative", "balanced", "aggressive"];

export interface TenantSummary {
  id: string;
  name: string;
  slug: string;
  risk_profile: RiskProfile;
  connections: { provider: "aws" | "azure"; name: string; agreement_type: string | null }[];
  latest_run: {
    id: string;
    history_days: number | null;
    projected_monthly_savings: number | null;
    effective_savings_rate_current_pct: number | null;
    effective_savings_rate_new_pct: number | null;
    warnings: string[];
  } | null;
}

export interface PlanItem {
  rank: number;
  action: string;
  description: string;
  term_months: number;
  payment_option: string | null;
  upfront_cost: number | null;
  monthly_savings: number;
  risk: string | null;
  urgent: boolean;
}

export interface ExpiringCommitment {
  id: string;
  kind: string;
  instance_type: string | null;
  quantity: number | null;
  hourly_commitment: number | null;
  end: string;
  days_left: number;
  urgent: boolean;
  monthly_savings_at_risk: number;
}

export interface FlaggedCommitment {
  id: string | null;
  kind: string;
  instance_type: string | null;
  status: string;
  utilization_pct: number | null;
  unused_monthly: number | null;
}

export interface ServiceSpend {
  provider: string;
  service: string;
  on_demand: number;
  committed: number;
  spot: number;
  on_demand_equiv: number;
}

export interface ComparisonRow {
  provider: string;
  kind: string;
  native_count: number;
  native_hourly_commitment: number;
  native_quantity: number;
  native_monthly_savings: number;
  engine_count: number;
  engine_hourly_commitment: number;
  engine_quantity: number;
  engine_monthly_savings: number;
}

export interface BacktestRow {
  plan_rank: number | null;
  action: string;
  kind: string;
  pool: string;
  instance_type: string | null;
  quantity: number | null;
  hourly_commitment: number | null;
  projected_utilization_pct: number;
  realized_utilization_pct: number;
  projected_monthly_savings: number;
  realized_monthly_savings: number;
}

export interface Backtest {
  available: boolean;
  reason?: string;
  train_until?: string;
  holdout_days?: number;
  recommendations?: BacktestRow[];
  projected_monthly_savings?: number;
  realized_monthly_savings?: number;
  savings_accuracy_pct?: number | null;
  projected_utilization_pct?: number;
  realized_utilization_pct?: number;
}

export interface Summary {
  as_of: string;
  history_days: number;
  risk_profile: RiskProfile;
  warnings: string[];
  on_demand_spend_monthly: number;
  on_demand_equiv_monthly: number;
  spot_spend_monthly: number;
  coverage_pct: number | null;
  existing_utilization_pct: number | null;
  expiring_commitments: ExpiringCommitment[];
  underutilized_commitments: FlaggedCommitment[];
  purchase_plan: PlanItem[];
  upfront_total: number;
  projected_monthly_savings: number;
  projected_annual_savings: number;
  expiring_savings_monthly: number;
  effective_savings_rate_current_pct: number | null;
  effective_savings_rate_new_pct: number | null;
  skipped_pools: { pool: string; reason: string }[];
  spend_by_service: ServiceSpend[];
  providers: Record<string, Record<string, number>>;
  native_comparison: { by_kind: ComparisonRow[]; explanations: string[] };
  backtest?: Backtest;
}

export interface AnalysisRun {
  id: string;
  tenant_id: string;
  status: string;
  risk_profile: RiskProfile | null;
  engine_version: string | null;
  finished_at: string | null;
  error: string | null;
  summary: Summary;
}

export interface TermOption {
  term_months: number;
  payment_option: string | null;
  discount: number;
  upfront: number;
  monthly_cost_after: number;
  monthly_savings: number;
  breakeven_month: number;
}

export interface ChartData {
  commitment_line: number;
  sizing_from: string;
  hourly_start: string;
  hourly: number[];
  daily_start: string;
  daily_min: number[];
  daily_mean: number[];
  daily_max: number[];
}

export interface RecommendationDetails {
  pool_label?: string;
  unit?: string;
  capacity_units?: number;
  purchase_units?: number;
  exchanged_units?: number;
  od_per_unit_hour?: number;
  percentiles?: Record<string, number>;
  stability?: {
    sizing_from: string;
    sizing_reason: string | null;
    step_day: string | null;
    step_change: number;
    trend: number;
    floor_cv: number;
    weekend_ratio: number | null;
    business_hours_ratio: number | null;
    night_ratio: number | null;
  };
  profiles?: Record<string, { capacity: number; utilization_pct: number; coverage_pct: number; monthly_savings: number }>;
  options?: TermOption[];
  expiring_commitments?: { id: string; end: string; quantity: number | null; hourly_commitment: number | null }[];
  renew_quantity?: number;
  add_quantity?: number;
  exchange_from?: string;
  from_instance_type?: string;
  chart?: ChartData;
  raw?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface Recommendation {
  id: string;
  source: "engine" | "native";
  action: "purchase" | "renew" | "exchange" | "flag";
  plan_rank: number | null;
  provider: "aws" | "azure";
  kind: string;
  scope: string | null;
  service: string | null;
  region: string | null;
  instance_family: string | null;
  instance_type: string | null;
  term_months: number;
  payment_option: string | null;
  hourly_commitment: number | null;
  quantity: number | null;
  upfront_cost: number | null;
  monthly_cost_after: number | null;
  monthly_savings: number;
  savings_pct: number | null;
  expected_utilization_pct: number | null;
  breakeven_month: number | null;
  risk: "low" | "med" | "high" | null;
  urgent: boolean;
  rationale: string | null;
  status: "open" | "accepted" | "dismissed";
  details: RecommendationDetails;
}

export interface DailyUsage {
  as_of: string | null;
  days: { date: string; on_demand: number; committed: number; unused: number; spot: number }[];
}

export interface Commitment {
  id: string;
  provider_commitment_id: string;
  provider: "aws" | "azure";
  kind: string;
  service: string | null;
  region: string | null;
  instance_type: string | null;
  instance_family: string | null;
  quantity: number | null;
  hourly_commitment: number | null;
  amortized_hourly_cost: number | null;
  term_months: number;
  payment_option: string | null;
  start_at: string;
  end_at: string;
  recent_utilization_pct: number | null;
  utilization: { date: string; utilization_pct: number; unused_cost: number | null }[];
}
