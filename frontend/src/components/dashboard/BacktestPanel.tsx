"use client";

import { Callout, Card, CardHeader, Stat } from "@/components/ui";
import { cn } from "@/lib/cn";
import { hourly, kindLabel, money, num, pct, poolLabel } from "@/lib/format";
import type { Backtest } from "@/lib/types";

function Delta({ projected, realized }: { projected: number; realized: number }) {
  const diff = realized - projected;
  const rel = projected ? diff / Math.abs(projected) : 0;
  return (
    <span className={cn("text-[12px]", Math.abs(rel) < 0.05 ? "text-muted" : diff >= 0 ? "text-good-text" : "text-critical")}>
      {diff >= 0 ? "+" : ""}
      {(rel * 100).toFixed(1)}%
    </span>
  );
}

export function BacktestPanel({ backtest }: { backtest?: Backtest }) {
  if (!backtest || !backtest.available) {
    return (
      <Callout tone="info" title="Backtest not available">
        {backtest?.reason ?? "Run an analysis to backtest the plan."}
      </Callout>
    );
  }
  const rows = [...(backtest.recommendations ?? [])].sort((a, b) => (a.plan_rank ?? 99) - (b.plan_rank ?? 99));
  return (
    <div className="space-y-5">
      <Callout tone="info" title="How this works">
        The engine was re-run on data up to {backtest.train_until} only. Its plan was then replayed hour by hour against the{" "}
        {backtest.holdout_days} days of actual usage that followed, which the engine never saw.
      </Callout>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Projected savings" value={money(backtest.projected_monthly_savings)} sub="per month, at sizing time" />
        <Stat label="Realized savings" value={money(backtest.realized_monthly_savings)} sub="per month, on the holdout" emphasis />
        <Stat label="Accuracy" value={pct(backtest.savings_accuracy_pct, 1)} sub="realized ÷ projected savings" />
        <Stat
          label="Utilization"
          value={pct(backtest.realized_utilization_pct, 1)}
          sub={`realized vs ${pct(backtest.projected_utilization_pct, 1)} projected (cost-weighted)`}
        />
      </div>
      <Card>
        <CardHeader title="Per recommendation" description="Projected at sizing time vs. realized on the holdout period" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-[13px] tabular">
            <thead className="text-left text-[12px] text-muted">
              <tr>
                <th className="px-5 py-2 font-medium" title="Rank in the plan sized on the training data">#</th>
                <th className="px-3 py-2 font-medium">Commitment</th>
                <th className="px-3 py-2 font-medium">Pool</th>
                <th className="px-3 py-2 text-right font-medium">Util. projected</th>
                <th className="px-3 py-2 text-right font-medium">Util. realized</th>
                <th className="px-3 py-2 text-right font-medium">Savings projected</th>
                <th className="px-3 py-2 text-right font-medium">Savings realized</th>
                <th className="px-5 py-2 text-right font-medium">Δ</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.pool}-${r.action}-${i}`} className="border-t border-border">
                  <td className="px-5 py-2 text-muted">{r.plan_rank}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium">
                      {r.action === "exchange" ? "Exchange → " : r.action === "renew" ? "Renew " : ""}
                      {kindLabel(r.kind)}
                    </div>
                    <div className="text-[12px] text-muted">
                      {r.hourly_commitment ? hourly(r.hourly_commitment) : `${num(r.quantity, 0)} × ${r.instance_type ?? ""}`}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-[12px] text-text-2">{poolLabel(r.pool)}</td>
                  <td className="px-3 py-2 text-right">{pct(r.projected_utilization_pct)}</td>
                  <td className={cn("px-3 py-2 text-right font-medium", r.realized_utilization_pct < 90 && "text-critical")}>
                    {pct(r.realized_utilization_pct)}
                  </td>
                  <td className="px-3 py-2 text-right">{money(r.projected_monthly_savings)}</td>
                  <td className="px-3 py-2 text-right font-medium">{money(r.realized_monthly_savings)}</td>
                  <td className="px-5 py-2 text-right">
                    <Delta projected={r.projected_monthly_savings} realized={r.realized_monthly_savings} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
