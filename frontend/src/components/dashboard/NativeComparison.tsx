"use client";

import { Card, CardHeader, Empty } from "@/components/ui";
import { hourly, isSavingsPlan, kindLabel, money, num, payment, term } from "@/lib/format";
import type { Recommendation, Summary } from "@/lib/types";

export function NativeComparison({ summary, recommendations }: { summary: Summary; recommendations: Recommendation[] }) {
  const cmp = summary.native_comparison;
  const native = recommendations.filter((r) => r.source === "native");
  const engineTotal = cmp.by_kind.reduce((s, r) => s + r.engine_monthly_savings, 0);
  const nativeTotal = cmp.by_kind.reduce((s, r) => s + r.native_monthly_savings, 0);
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Engine vs. provider recommendations"
          description={`Engine ${money(engineTotal)}/mo vs native ${money(nativeTotal)}/mo of projected savings`}
        />
        {cmp.by_kind.length ? (
          <div className="overflow-x-auto px-5 pb-4">
            <table className="w-full min-w-[720px] text-[13px] tabular">
              <thead className="text-left text-[12px] text-muted">
                <tr>
                  <th className="py-1.5 font-medium">Commitment type</th>
                  <th className="py-1.5 text-right font-medium">Native: size</th>
                  <th className="py-1.5 text-right font-medium">Engine: size</th>
                  <th className="py-1.5 text-right font-medium">Native savings / mo</th>
                  <th className="py-1.5 text-right font-medium">Engine savings / mo</th>
                </tr>
              </thead>
              <tbody>
                {cmp.by_kind.map((r) => {
                  const sp = isSavingsPlan(r.kind);
                  return (
                    <tr key={`${r.provider}-${r.kind}`} className="border-t border-border">
                      <td className="py-2">
                        {r.provider.toUpperCase()} · {kindLabel(r.kind)}
                      </td>
                      <td className="py-2 text-right">
                        {r.native_count ? (sp ? hourly(r.native_hourly_commitment) : `${num(r.native_quantity, 0)} reserved`) : "—"}
                        <span className="ml-1 text-[11px] text-muted">({r.native_count})</span>
                      </td>
                      <td className="py-2 text-right">
                        {r.engine_count ? (sp ? hourly(r.engine_hourly_commitment) : `${num(r.engine_quantity, 0)} reserved`) : "—"}
                        <span className="ml-1 text-[11px] text-muted">({r.engine_count})</span>
                      </td>
                      <td className="py-2 text-right">{money(r.native_monthly_savings)}</td>
                      <td className="py-2 text-right font-medium text-good-text">{money(r.engine_monthly_savings)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No recommendations from either source.</Empty>
        )}
      </Card>

      <Card>
        <CardHeader title="Why they differ" description="Reasons stated by the engine for this client" />
        <ul className="space-y-2 px-5 pb-4 text-[13.5px] leading-relaxed">
          {cmp.explanations.map((e) => (
            <li key={e} className="flex gap-2">
              <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
              <span>{e}</span>
            </li>
          ))}
          {cmp.explanations.length === 0 && <li className="text-muted">No differences to explain.</li>}
        </ul>
      </Card>

      <Card>
        <CardHeader title="Native recommendations as received" description="Shown for reference; not part of the purchase plan" />
        {native.length ? (
          <div className="overflow-x-auto px-5 pb-4">
            <table className="w-full min-w-[640px] text-[13px] tabular">
              <thead className="text-left text-[12px] text-muted">
                <tr>
                  <th className="py-1.5 font-medium">Type</th>
                  <th className="py-1.5 font-medium">Region</th>
                  <th className="py-1.5 font-medium">Size</th>
                  <th className="py-1.5 font-medium">Term · payment</th>
                  <th className="py-1.5 text-right font-medium">Savings / mo</th>
                </tr>
              </thead>
              <tbody>
                {native.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="py-2">
                      {r.provider.toUpperCase()} · {kindLabel(r.kind)}
                    </td>
                    <td className="py-2 text-text-2">{r.region ?? r.scope ?? "—"}</td>
                    <td className="py-2">
                      {r.hourly_commitment ? hourly(r.hourly_commitment) : `${num(r.quantity, 0)} × ${r.instance_type ?? ""}`}
                    </td>
                    <td className="py-2 text-text-2">
                      {term(r.term_months)} · {payment(r.payment_option)}
                    </td>
                    <td className="py-2 text-right">{money(r.monthly_savings)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No native recommendations were collected.</Empty>
        )}
      </Card>
    </div>
  );
}
