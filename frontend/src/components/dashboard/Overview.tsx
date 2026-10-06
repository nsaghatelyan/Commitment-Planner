"use client";

import { ArrowRight } from "lucide-react";

import { SpendByService } from "@/components/charts/SpendByService";
import { SpendDailyChart } from "@/components/charts/SpendDailyChart";
import { Callout, Card, CardHeader, Empty, Stat } from "@/components/ui";
import { money, pct } from "@/lib/format";
import type { DailyUsage, Summary } from "@/lib/types";

export function Overview({ summary, daily, onOpenPlan }: { summary: Summary; daily: DailyUsage | null; onOpenPlan: () => void }) {
  const bt = summary.backtest;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Commitment-eligible spend"
          value={money(summary.on_demand_equiv_monthly, { compact: true })}
          sub={`per month at on-demand rates · ${money(summary.on_demand_spend_monthly, { compact: true })} billed on demand`}
        />
        <Stat
          label="Effective savings rate"
          value={
            <span className="flex items-baseline gap-1.5">
              <span className="text-text-2">{pct(summary.effective_savings_rate_current_pct, 0)}</span>
              <ArrowRight aria-label="after the plan" className="h-4 w-4 self-center text-muted" />
              <span className="text-accent">{pct(summary.effective_savings_rate_new_pct, 0)}</span>
            </span>
          }
          sub="today → after the plan"
        />
        <Stat
          label="Projected savings"
          value={money(summary.projected_monthly_savings, { compact: true })}
          sub={`per month · ${money(summary.projected_annual_savings, { compact: true })} per year`}
          emphasis
        />
        <Stat label="Commitment coverage" value={pct(summary.coverage_pct, 0)} sub="of commitment-eligible spend" />
        <Stat
          label="Utilization of existing commitments"
          value={pct(summary.existing_utilization_pct, 0)}
          sub="last 30 days"
        />
        <Stat
          label="Backtest accuracy"
          value={bt?.available ? pct(bt.savings_accuracy_pct, 0) : "—"}
          sub={
            bt?.available
              ? `realized vs projected savings, ${bt.holdout_days}-day holdout`
              : bt?.reason ?? "not run"
          }
        />
      </div>

      {(summary.warnings.length > 0 || summary.expiring_commitments.some((e) => e.urgent) || summary.underutilized_commitments.length > 0) && (
        <div className="grid gap-3 lg:grid-cols-2">
          {summary.warnings.map((w) => (
            <Callout key={w} tone="warning" title="Short usage history">
              {w}
            </Callout>
          ))}
          {summary.expiring_commitments
            .filter((e) => e.urgent)
            .map((e) => (
              <Callout key={e.id} tone="critical" title={`Commitment ends in ${e.days_left} days`}>
                {e.instance_type ?? e.kind} {e.hourly_commitment ? `($${e.hourly_commitment.toFixed(3)}/h)` : ""} ends on {e.end}.{" "}
                {money(e.monthly_savings_at_risk)}/month of savings is at risk; its renewal is first in the plan.
              </Callout>
            ))}
          {summary.underutilized_commitments.map((u) => (
            <Callout key={u.id ?? u.instance_type ?? u.kind} tone="warning" title={`${u.instance_type ?? u.kind}: ${u.status}`}>
              {pct(u.utilization_pct, 0)} utilized over the last 14 days
              {u.unused_monthly ? `, wasting about ${money(u.unused_monthly)}/month` : ""}. See the purchase plan for the exchange or follow-up.
            </Callout>
          ))}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader
            title="How eligible usage was paid for"
            description="Daily cost of commitment-eligible services over the last 90 days"
          />
          <div className="px-5 pb-4">{daily?.days.length ? <SpendDailyChart data={daily.days} /> : <Empty>No usage data.</Empty>}</div>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader title="Spend split" description="Last 30 days, per month" />
          <div className="px-5 pb-4">
            {summary.spend_by_service.length ? <SpendByService rows={summary.spend_by_service} /> : <Empty>No usage data.</Empty>}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Purchase plan at a glance"
          description={`${summary.purchase_plan.length} steps · ${money(summary.upfront_total)} upfront`}
          action={
            <button onClick={onOpenPlan} className="text-[13px] font-medium text-accent hover:underline">
              Open plan →
            </button>
          }
        />
        <ol className="divide-y divide-border px-5 pb-3">
          {summary.purchase_plan.slice(0, 6).map((p) => (
            <li key={p.rank} className="flex items-center justify-between gap-4 py-2 text-[13px]">
              <span className="flex min-w-0 items-center gap-3">
                <span className="w-5 text-right text-muted tabular">{p.rank}</span>
                <span className="truncate first-letter:uppercase">{p.description}</span>
              </span>
              <span className="shrink-0 font-medium tabular text-good-text">+{money(p.monthly_savings)}/mo</span>
            </li>
          ))}
          {summary.purchase_plan.length === 0 && (
            <Empty>
              No commitments recommended: there isn&apos;t enough steady usage to commit to
              {summary.skipped_pools.length ? " (see Not committed for each pool's reason)" : ""}.
            </Empty>
          )}
        </ol>
      </Card>
    </div>
  );
}
