"use client";

import { Check, X } from "lucide-react";

import { BaselineChart } from "@/components/charts/BaselineChart";
import { HistoryChart } from "@/components/charts/HistoryChart";
import { ActionBadge, Badge, Button, Card, CardHeader, RiskBadge, Sheet } from "@/components/ui";
import { cn } from "@/lib/cn";
import { kindLabel, money, num, payment, pct, serviceLabel, term } from "@/lib/format";
import type { Recommendation, RiskProfile } from "@/lib/types";

import { describeCommitment } from "./PlanTable";

function fmtUnit(v: number | undefined, unit: string) {
  if (v === undefined) return "—";
  return unit.startsWith("$") ? `$${v.toFixed(3)}/h` : `${num(v, 1)}`;
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-medium tracking-wide text-muted uppercase">{label}</div>
      <div className="mt-0.5 text-[14px] font-semibold tabular">{value}</div>
    </div>
  );
}

function ratioText(v: number | null | undefined, high: string, low: string) {
  if (v === null || v === undefined) return "—";
  if (!Number.isFinite(v)) return `${high} only`;
  return `${v.toFixed(2)}×${v > 1.5 ? ` (${high})` : v < 0.67 ? ` (${low})` : ""}`;
}

export function RecommendationDetail({
  rec,
  activeProfile,
  onClose,
  onStatus,
}: {
  rec: Recommendation | null;
  activeProfile: RiskProfile;
  onClose: () => void;
  onStatus: (r: Recommendation, s: Recommendation["status"]) => void;
}) {
  if (!rec) return null;
  const d = rec.details;
  const unit = d.unit ?? "";
  const chart = d.chart;
  const st = d.stability;
  return (
    <Sheet
      open
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          {rec.plan_rank && <span className="text-muted">#{rec.plan_rank}</span>}
          <ActionBadge action={rec.action} urgent={rec.urgent} />
          {kindLabel(rec.kind)} · {describeCommitment(rec)}
          <span className="font-normal text-text-2">
            {rec.region ? `in ${rec.region}` : ""} {rec.service ? `· ${serviceLabel(rec.service)}` : ""}
          </span>
        </span>
      }
    >
      <div className="space-y-5">
        <Card className="px-5 py-4">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-7">
            <Fact label="Term · payment" value={`${term(rec.term_months)} · ${payment(rec.payment_option)}`} />
            <Fact label="Savings / month" value={<span className="text-good-text">{money(rec.monthly_savings)}</span>} />
            <Fact label="Savings %" value={pct(rec.savings_pct, 0)} />
            <Fact label="Upfront" value={rec.upfront_cost ? money(rec.upfront_cost) : "—"} />
            <Fact label="Expected util." value={pct(rec.expected_utilization_pct)} />
            <Fact label="Break-even" value={rec.breakeven_month ? `month ${num(rec.breakeven_month, 1)}` : "immediate"} />
            <Fact label="Risk" value={<RiskBadge risk={rec.risk} />} />
          </div>
          {rec.rationale && <p className="mt-4 border-t border-border pt-3 text-[13.5px] leading-relaxed text-text">{rec.rationale}</p>}
          {rec.source === "engine" && rec.action !== "flag" && (
            <div className="mt-3 flex gap-2">
              <Button variant={rec.status === "accepted" ? "primary" : "secondary"} size="sm" onClick={() => onStatus(rec, rec.status === "accepted" ? "open" : "accepted")}>
                <Check className="h-3.5 w-3.5" /> {rec.status === "accepted" ? "Accepted" : "Accept"}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => onStatus(rec, rec.status === "dismissed" ? "open" : "dismissed")}>
                <X className="h-3.5 w-3.5" /> {rec.status === "dismissed" ? "Dismissed" : "Dismiss"}
              </Button>
            </div>
          )}
        </Card>

        {chart && (
          <Card>
            <CardHeader
              title="Hourly baseline vs. the commitment"
              description={`Last ${Math.round(chart.hourly.length / 24)} days of the pool's uncovered usage, in ${unit}`}
            />
            <div className="px-5 pb-4">
              <BaselineChart chart={chart} unit={unit} />
            </div>
          </Card>
        )}

        {chart && st && (
          <Card>
            <CardHeader
              title="Full history and what the engine sized on"
              description="Daily floor, mean and peak; the shaded window is the data used for sizing"
            />
            <div className="px-5 pb-4">
              <HistoryChart chart={chart} unit={unit} stepDay={st.step_day} sizingReason={st.sizing_reason} />
            </div>
          </Card>
        )}

        {st && d.percentiles && (
          <div className="grid gap-5 lg:grid-cols-2">
            <Card>
              <CardHeader title="Distribution of hourly usage" description={`Sizing window from ${st.sizing_from}`} />
              <div className="px-5 pb-4">
                <table className="w-full text-[13px] tabular">
                  <tbody>
                    {Object.entries(d.percentiles).map(([p, v]) => {
                      const line = d.capacity_units ?? 0;
                      const max = Math.max(...Object.values(d.percentiles!), line) || 1;
                      return (
                        <tr key={p} className="border-t border-border first:border-0">
                          <td className="w-12 py-1.5 font-medium text-text-2 uppercase">{p}</td>
                          <td className="py-1.5">
                            <div className="relative h-2 rounded bg-surface-2">
                              <div className="absolute inset-y-0 left-0 rounded bg-[var(--c-committed)]" style={{ width: `${(v / max) * 100}%` }} />
                              <div className="absolute -inset-y-1 w-0.5 bg-text" style={{ left: `${(line / max) * 100}%` }} title="commitment" />
                            </div>
                          </td>
                          <td className="w-24 py-1.5 text-right">{fmtUnit(v, unit)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <p className="mt-2 text-[12px] text-muted">
                  Black marker: commitment ({fmtUnit(d.capacity_units, unit)}
                  {d.exchanged_units ? `, of which ${fmtUnit(d.exchanged_units, unit)} via exchange` : ""}).
                </p>
              </div>
            </Card>
            <Card>
              <CardHeader title="Stability" description="Signals the engine checked before committing" />
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 px-5 pb-4 text-[13px]">
                <dt className="text-text-2">Sized from</dt>
                <dd className="text-right font-medium">
                  {st.sizing_from}
                  {st.sizing_reason && <Badge className="ml-1.5">{st.sizing_reason}</Badge>}
                </dd>
                <dt className="text-text-2">Step change</dt>
                <dd className="text-right font-medium">{st.step_day ? `${st.step_day} (${(st.step_change * 100).toFixed(0)}%)` : "none"}</dd>
                <dt className="text-text-2">Trend over window</dt>
                <dd className="text-right font-medium">{(st.trend * 100).toFixed(1)}%</dd>
                <dt className="text-text-2">Daily floor variation (CV)</dt>
                <dd className="text-right font-medium">{st.floor_cv.toFixed(3)}</dd>
                <dt className="text-text-2">Weekend vs weekday</dt>
                <dd className="text-right font-medium">{ratioText(st.weekend_ratio, "weekend-heavy", "weekday-heavy")}</dd>
                <dt className="text-text-2">Business hours vs other</dt>
                <dd className="text-right font-medium">{ratioText(st.business_hours_ratio, "daytime peaks", "off-hours")}</dd>
                <dt className="text-text-2">Night vs day</dt>
                <dd className="text-right font-medium">{ratioText(st.night_ratio, "nightly batch", "daytime")}</dd>
              </dl>
            </Card>
          </div>
        )}

        {d.profiles && Object.keys(d.profiles).length > 0 && (
          <Card>
            <CardHeader
              title="Simulated by risk profile"
              description={`Same pool, each profile's sizing rule, simulated hour by hour (1-year base term)${
                d.exchanged_units ? "; sizes include the exchanged reservation" : ""
              }`}
            />
            <table className="mx-5 mb-4 w-[calc(100%-2.5rem)] text-[13px] tabular">
              <thead className="text-left text-[12px] text-muted">
                <tr>
                  <th className="py-1.5 font-medium">Profile</th>
                  <th className="py-1.5 text-right font-medium">Commitment ({unit})</th>
                  <th className="py-1.5 text-right font-medium">Utilization</th>
                  <th className="py-1.5 text-right font-medium">Coverage</th>
                  <th className="py-1.5 text-right font-medium">Savings / mo</th>
                </tr>
              </thead>
              <tbody>
                {(["conservative", "balanced", "aggressive"] as const).map((p) => {
                  const s = d.profiles?.[p];
                  return (
                    <tr key={p} className={cn("border-t border-border", p === activeProfile && "bg-accent-soft")}>
                      <td className="py-1.5 capitalize">
                        {p} {p === activeProfile && <Badge tone="accent">current</Badge>}
                      </td>
                      <td className="py-1.5 text-right">{s ? fmtUnit(s.capacity, unit) : "none"}</td>
                      <td className="py-1.5 text-right">{s ? pct(s.utilization_pct) : "—"}</td>
                      <td className="py-1.5 text-right">{s ? pct(s.coverage_pct, 0) : "—"}</td>
                      <td className="py-1.5 text-right text-good-text">{s ? money(s.monthly_savings) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        )}

        {d.options && d.options.length > 0 && (
          <Card>
            <CardHeader title="Term and payment options" description="Priced from the price table for the same size" />
            <div className="overflow-x-auto px-5 pb-4">
              <table className="w-full min-w-[640px] text-[13px] tabular">
                <thead className="text-left text-[12px] text-muted">
                  <tr>
                    <th className="py-1.5 font-medium">Term</th>
                    <th className="py-1.5 font-medium">Payment</th>
                    <th className="py-1.5 text-right font-medium">Discount</th>
                    <th className="py-1.5 text-right font-medium">Upfront</th>
                    <th className="py-1.5 text-right font-medium">Monthly cost</th>
                    <th className="py-1.5 text-right font-medium">Savings / mo</th>
                    <th className="py-1.5 text-right font-medium">Break-even</th>
                  </tr>
                </thead>
                <tbody>
                  {d.options.map((o) => {
                    const chosen = o.term_months === rec.term_months && o.payment_option === rec.payment_option;
                    return (
                      <tr key={`${o.term_months}-${o.payment_option}`} className={cn("border-t border-border", chosen && "bg-accent-soft font-medium")}>
                        <td className="py-1.5">
                          {term(o.term_months)} {chosen && <Badge tone="accent">recommended</Badge>}
                        </td>
                        <td className="py-1.5">{payment(o.payment_option)}</td>
                        <td className="py-1.5 text-right">{pct(o.discount * 100, 0)}</td>
                        <td className="py-1.5 text-right">{o.upfront ? money(o.upfront) : "—"}</td>
                        <td className="py-1.5 text-right">{money(o.monthly_cost_after)}</td>
                        <td className="py-1.5 text-right text-good-text">{money(o.monthly_savings)}</td>
                        <td className="py-1.5 text-right">{o.breakeven_month ? `month ${num(o.breakeven_month, 1)}` : "immediate"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        {d.expiring_commitments && d.expiring_commitments.length > 0 && (
          <Card className="px-5 py-4 text-[13px]">
            <div className="font-semibold">Replaces expiring commitments</div>
            <ul className="mt-1.5 space-y-1 text-text-2">
              {d.expiring_commitments.map((c) => (
                <li key={c.id}>
                  {c.quantity ? `${c.quantity} reserved` : c.hourly_commitment ? `$${c.hourly_commitment}/h` : c.id} — ends {c.end}
                </li>
              ))}
            </ul>
            {d.renew_quantity !== undefined && (
              <p className="mt-2">
                Renew <b>{d.renew_quantity}</b>
                {d.add_quantity ? (
                  <>
                    {" "}
                    and add <b>{d.add_quantity}</b>
                  </>
                ) : null}
                .
              </p>
            )}
          </Card>
        )}
      </div>
    </Sheet>
  );
}
