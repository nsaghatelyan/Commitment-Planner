"use client";

import { Clock, Moon, Sparkles, Sun, TrendingDown, Zap } from "lucide-react";

import { Card, CardHeader, Empty } from "@/components/ui";
import { money, poolLabel } from "@/lib/format";
import type { Summary } from "@/lib/types";

const GROUPS = [
  { match: /business-hours|weekday/, label: "Business hours / weekdays only", icon: Sun, why: "Usage disappears outside working hours, so a 24/7 commitment would sit idle." },
  { match: /nightly/, label: "Nightly batch", icon: Moon, why: "Runs only a few hours a night; there is no around-the-clock floor to commit to." },
  { match: /too new/, label: "Too new", icon: Sparkles, why: "Workloads need to run for a while before their floor is known." },
  { match: /decommission|stopped/, label: "Decommissioned or migrated away", icon: TrendingDown, why: "Usage stopped; buying here would strand the commitment." },
  { match: /settle|changed/, label: "Recently changed", icon: Clock, why: "A step change happened too recently to size on the new level." },
] as const;

export function ExcludedPanel({ summary }: { summary: Summary }) {
  const pools = summary.skipped_pools;
  // Each pool goes to the first group whose pattern matches its reason.
  const groupOf = (reason: string) => GROUPS.findIndex((g) => g.match.test(reason));
  const grouped = GROUPS.map((g, i) => ({ ...g, pools: pools.filter((p) => groupOf(p.reason) === i) }));
  const other = pools.filter((p) => groupOf(p.reason) === -1);
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Deliberately not committed"
          description="Usage the engine looked at and chose not to cover, with the reason"
        />
        <div className="grid gap-3 px-5 pb-5 md:grid-cols-2">
          <div className="rounded-lg border border-border p-4">
            <div className="flex items-center gap-2 font-semibold">
              <Zap aria-hidden className="h-4 w-4 text-[var(--c-spot)]" /> Spot usage
            </div>
            <p className="mt-1 text-[13px] text-text-2">
              {money(summary.spot_spend_monthly)}/month runs on Spot. It is already discounted and can be interrupted, so it is excluded from every baseline.
            </p>
          </div>
          {grouped
            .filter((g) => g.pools.length)
            .map((g) => (
              <div key={g.label} className="rounded-lg border border-border p-4">
                <div className="flex items-center gap-2 font-semibold">
                  <g.icon aria-hidden className="h-4 w-4 text-muted" /> {g.label}
                  <span className="text-[12px] font-normal text-muted">({g.pools.length})</span>
                </div>
                <p className="mt-1 text-[13px] text-text-2">{g.why}</p>
                <ul className="mt-2 space-y-1 text-[12.5px]">
                  {g.pools.map((p) => (
                    <li key={p.pool} className="flex justify-between gap-3">
                      <span className="font-medium">{poolLabel(p.pool)}</span>
                      <span className="text-right text-muted">{p.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
        </div>
      </Card>
      {other.length > 0 && (
        <Card>
          <CardHeader title="Other pools without a commitment" description="Covered by earlier layers, no prices, or too small to matter" />
          <ul className="divide-y divide-border px-5 pb-3 text-[13px]">
            {other.map((p) => (
              <li key={p.pool} className="flex justify-between gap-4 py-2">
                <span>{poolLabel(p.pool)}</span>
                <span className="text-right text-muted">{p.reason}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {pools.length === 0 && <Empty>The engine committed to every pool it analysed.</Empty>}
    </div>
  );
}
