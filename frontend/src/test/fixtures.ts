import type { Recommendation } from "@/lib/types";

const chart = {
  commitment_line: 10,
  sizing_from: "2026-08-01",
  hourly_start: "2026-09-01T00:00:00+00:00",
  hourly: Array.from({ length: 48 }, (_, i) => 10 + (i % 24 > 8 && i % 24 < 18 ? 4 : 0)),
  daily_start: "2026-07-01",
  daily_min: Array.from({ length: 60 }, () => 10),
  daily_mean: Array.from({ length: 60 }, () => 12),
  daily_max: Array.from({ length: 60 }, () => 14),
};

export function rec(overrides: Partial<Recommendation> = {}): Recommendation {
  return {
    id: overrides.id ?? "r1",
    source: "engine",
    action: "purchase",
    plan_rank: 1,
    provider: "aws",
    kind: "aws_ri",
    scope: "Region",
    service: "rds",
    region: "us-east-1",
    instance_family: "r6g",
    instance_type: "db.r6g.xlarge",
    term_months: 12,
    payment_option: "no_upfront",
    hourly_commitment: null,
    quantity: 1,
    upfront_cost: 0,
    monthly_cost_after: 420,
    monthly_savings: 257,
    savings_pct: 38,
    expected_utilization_pct: 100,
    breakeven_month: 0,
    risk: "low",
    urgent: false,
    rationale: "Your RDS r6g usage in us-east-1 never dropped below 16 normalized units/hour.",
    status: "open",
    details: {
      unit: "normalized units",
      capacity_units: 10,
      percentiles: { p5: 10, p10: 10, p20: 10, p30: 10, p40: 11, p50: 12 },
      stability: {
        sizing_from: "2026-08-01",
        sizing_reason: "step",
        step_day: "2026-08-01",
        step_change: 0.5,
        trend: 0.01,
        floor_cv: 0,
        weekend_ratio: 1,
        business_hours_ratio: 1.2,
        night_ratio: 0.9,
      },
      profiles: {
        conservative: { capacity: 10, utilization_pct: 100, coverage_pct: 80, monthly_savings: 200 },
        balanced: { capacity: 10, utilization_pct: 100, coverage_pct: 80, monthly_savings: 257 },
      },
      options: [
        { term_months: 12, payment_option: "no_upfront", discount: 0.38, upfront: 0, monthly_cost_after: 420, monthly_savings: 257, breakeven_month: 0 },
        { term_months: 36, payment_option: "all_upfront", discount: 0.62, upfront: 9000, monthly_cost_after: 250, monthly_savings: 427, breakeven_month: 13 },
      ],
      chart,
    },
    ...overrides,
  };
}
