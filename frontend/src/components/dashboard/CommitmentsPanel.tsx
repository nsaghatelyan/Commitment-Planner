"use client";

import { UtilizationSparkline } from "@/components/charts/UtilizationSparkline";
import { Badge, Card, CardHeader, Empty } from "@/components/ui";
import { hourly, kindLabel, money, num, payment, pct, shortDate, term } from "@/lib/format";
import type { Commitment, Summary } from "@/lib/types";

function status(c: Commitment, summary: Summary) {
  const flagged = summary.underutilized_commitments.find((u) => u.id === c.provider_commitment_id);
  const expiring = summary.expiring_commitments.find((e) => e.id === c.provider_commitment_id);
  const out: { label: string; tone: "critical" | "warning" | "good" | "neutral" }[] = [];
  if (expiring) out.push({ label: expiring.urgent ? `ends in ${expiring.days_left}d` : `expiring · ${expiring.days_left}d`, tone: expiring.urgent ? "critical" : "warning" });
  if (flagged) out.push({ label: flagged.status.startsWith("stranded") ? "stranded" : "underutilized", tone: "critical" });
  else if ((c.recent_utilization_pct ?? 100) < 80) out.push({ label: "underutilized", tone: "warning" });
  if (!out.length) out.push({ label: "healthy", tone: "good" });
  return out;
}

export function CommitmentsPanel({ commitments, summary }: { commitments: Commitment[] | null; summary: Summary }) {
  return (
    <Card>
      <CardHeader
        title="Existing commitments"
        description="Daily utilization over the last 90 days; dashed line at 80%"
      />
      {commitments && commitments.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-[13px] tabular">
            <thead className="text-left text-[12px] text-muted">
              <tr>
                <th className="px-5 py-2 font-medium">Commitment</th>
                <th className="px-3 py-2 font-medium">Scope</th>
                <th className="px-3 py-2 text-right font-medium">Size</th>
                <th className="px-3 py-2 text-right font-medium">Cost / mo</th>
                <th className="px-3 py-2 font-medium">Ends</th>
                <th className="px-3 py-2 font-medium">Utilization</th>
                <th className="px-3 py-2 text-right font-medium">Last 14d</th>
                <th className="px-5 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {commitments.map((c) => (
                <tr key={c.id} className="border-t border-border">
                  <td className="px-5 py-2">
                    <div className="font-medium">{kindLabel(c.kind)}</div>
                    <div className="text-[12px] text-muted">
                      {c.provider.toUpperCase()} · {term(c.term_months)} · {payment(c.payment_option)}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-text-2">{c.region ?? "All regions"}</td>
                  <td className="px-3 py-2 text-right">
                    {c.hourly_commitment !== null && c.kind.includes("_sp_") ? hourly(c.hourly_commitment) : `${num(c.quantity, 0)} × ${c.instance_type}`}
                  </td>
                  <td className="px-3 py-2 text-right">{money((c.amortized_hourly_cost ?? 0) * 730)}</td>
                  <td className="px-3 py-2">{shortDate(c.end_at)} {c.end_at.slice(0, 4)}</td>
                  <td className="px-3 py-2">
                    <UtilizationSparkline points={c.utilization} />
                  </td>
                  <td className="px-3 py-2 text-right font-medium">{pct(c.recent_utilization_pct, 0)}</td>
                  <td className="px-5 py-2">
                    <span className="flex flex-wrap gap-1">
                      {status(c, summary).map((s) => (
                        <Badge key={s.label} tone={s.tone}>
                          {s.label}
                        </Badge>
                      ))}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>{commitments ? "This client has no savings plans or reservations yet." : "Loading…"}</Empty>
      )}
    </Card>
  );
}
