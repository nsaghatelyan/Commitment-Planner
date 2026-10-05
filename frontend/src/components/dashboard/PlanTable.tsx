"use client";

import { ArrowDown, ArrowUp, Download, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { ActionBadge, Badge, Button, Card, Empty, RiskBadge, Segmented } from "@/components/ui";
import { exportCsv, exportXlsx } from "@/lib/export";
import { hourly, isSavingsPlan, kindLabel, money, num, payment, pct, serviceLabel, term } from "@/lib/format";
import type { Recommendation } from "@/lib/types";

type SortKey = "plan_rank" | "monthly_savings" | "upfront_cost" | "expected_utilization_pct" | "breakeven_month";
type TypeFilter = "all" | "ri" | "sp" | "renew" | "exchange";

const TYPE_OPTIONS: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "ri", label: "Reservations" },
  { value: "sp", label: "Savings plans" },
  { value: "renew", label: "Renewals" },
  { value: "exchange", label: "Exchanges" },
];

export function describeCommitment(r: Recommendation) {
  if (r.hourly_commitment !== null && isSavingsPlan(r.kind)) return hourly(r.hourly_commitment);
  return `${num(r.quantity, 2)} × ${r.instance_type ?? r.instance_family ?? ""}`;
}

export function PlanTable({
  recommendations,
  tenantName,
  onSelect,
}: {
  recommendations: Recommendation[];
  tenantName: string;
  onSelect: (r: Recommendation) => void;
}) {
  const [cloud, setCloud] = useState<"all" | "aws" | "azure">("all");
  const [type, setType] = useState<TypeFilter>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "plan_rank", dir: 1 });

  const plan = recommendations.filter((r) => r.source === "engine" && r.plan_rank !== null);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return plan
      .filter((r) => cloud === "all" || r.provider === cloud)
      .filter((r) =>
        type === "all"
          ? true
          : type === "ri"
            ? !isSavingsPlan(r.kind)
            : type === "sp"
              ? isSavingsPlan(r.kind)
              : r.action === type,
      )
      .filter(
        (r) =>
          !q ||
          [r.instance_type, r.instance_family, r.service, r.region, kindLabel(r.kind), r.rationale]
            .filter(Boolean)
            .some((s) => String(s).toLowerCase().includes(q)),
      )
      .sort((a, b) => ((a[sort.key] ?? 0) - (b[sort.key] ?? 0)) * sort.dir);
  }, [plan, cloud, type, query, sort]);

  const totals = rows.reduce(
    (t, r) => ({ savings: t.savings + r.monthly_savings, upfront: t.upfront + (r.upfront_cost ?? 0) }),
    { savings: 0, upfront: 0 },
  );
  const file = `${tenantName.toLowerCase().replace(/\s+/g, "-")}-purchase-plan`;

  const Th = ({ k, children, className = "" }: { k: SortKey; children: React.ReactNode; className?: string }) => (
    <th className={`px-3 py-2 font-medium ${className}`}>
      <button
        className="inline-flex items-center gap-1 hover:text-text"
        onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? (-s.dir as 1 | -1) : k === "plan_rank" ? 1 : -1 }))}
        aria-label={`Sort by ${String(children)}`}
      >
        {children}
        {sort.key === k && (sort.dir === 1 ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}
      </button>
    </th>
  );

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <Segmented
          label="Cloud"
          value={cloud}
          onChange={setCloud}
          options={[
            { value: "all", label: "All clouds" },
            { value: "aws", label: "AWS" },
            { value: "azure", label: "Azure" },
          ]}
        />
        <Segmented label="Type" value={type} onChange={setType} options={TYPE_OPTIONS} />
        <label className="relative ml-auto">
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search type, region…"
            aria-label="Search recommendations"
            className="h-8 w-52 rounded-lg border border-border bg-surface pr-2 pl-7 text-[13px] outline-none focus:border-accent"
          />
        </label>
        <Button size="md" onClick={() => exportCsv(rows, `${file}.csv`)}>
          <Download className="h-3.5 w-3.5" /> CSV
        </Button>
        <Button size="md" onClick={() => exportXlsx(rows, `${file}.xlsx`)}>
          <Download className="h-3.5 w-3.5" /> XLSX
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1080px] text-[13px]">
          <thead className="text-left text-[12px] text-muted">
            <tr>
              <Th k="plan_rank" className="w-12">#</Th>
              <th className="px-3 py-2 font-medium">Action</th>
              <th className="px-3 py-2 font-medium">Commitment</th>
              <th className="px-3 py-2 font-medium">Scope</th>
              <th className="px-3 py-2 font-medium">Term · payment</th>
              <th className="px-3 py-2 text-right font-medium">Size</th>
              <Th k="upfront_cost" className="text-right">Upfront</Th>
              <Th k="monthly_savings" className="text-right">Savings / mo</Th>
              <Th k="expected_utilization_pct" className="text-right">Exp. util.</Th>
              <Th k="breakeven_month" className="text-right">Break-even</Th>
              <th className="px-3 py-2 font-medium">Risk</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                onClick={() => onSelect(r)}
                className="cursor-pointer border-t border-border hover:bg-surface-2"
                tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onSelect(r)}
              >
                <td className="px-3 py-2.5 text-muted tabular">{r.plan_rank}</td>
                <td className="px-3 py-2.5">
                  <ActionBadge action={r.action} urgent={r.urgent} />
                </td>
                <td className="px-3 py-2.5">
                  <div className="font-medium">{kindLabel(r.kind)}</div>
                  <div className="text-[12px] text-muted">
                    {r.provider.toUpperCase()} · {serviceLabel(r.service)}
                    {r.status !== "open" && (
                      <Badge tone={r.status === "accepted" ? "good" : "neutral"} className="ml-1.5">
                        {r.status}
                      </Badge>
                    )}
                  </div>
                </td>
                <td className="px-3 py-2.5 text-text-2">
                  {r.region ?? (r.scope === "organization" ? "All regions" : r.scope ?? "Shared")}
                </td>
                <td className="px-3 py-2.5 text-text-2">
                  {term(r.term_months)} · {payment(r.payment_option)}
                </td>
                <td className="px-3 py-2.5 text-right font-medium tabular">{describeCommitment(r)}</td>
                <td className="px-3 py-2.5 text-right tabular">{r.upfront_cost ? money(r.upfront_cost) : "—"}</td>
                <td className="px-3 py-2.5 text-right font-medium text-good-text tabular">{money(r.monthly_savings)}</td>
                <td className="px-3 py-2.5 text-right tabular">{pct(r.expected_utilization_pct)}</td>
                <td className="px-3 py-2.5 text-right tabular">
                  {r.breakeven_month ? `month ${num(r.breakeven_month, 1)}` : "immediate"}
                </td>
                <td className="px-3 py-2.5">
                  <RiskBadge risk={r.risk} />
                </td>
              </tr>
            ))}
          </tbody>
          {rows.length > 0 && (
            <tfoot>
              <tr className="border-t border-border bg-surface-2/60 text-[13px] font-semibold">
                <td colSpan={6} className="px-3 py-2.5 text-text-2">
                  {rows.length} recommendation{rows.length === 1 ? "" : "s"}
                </td>
                <td className="px-3 py-2.5 text-right tabular">{money(totals.upfront)}</td>
                <td className="px-3 py-2.5 text-right text-good-text tabular">{money(totals.savings)}</td>
                <td colSpan={3} />
              </tr>
            </tfoot>
          )}
        </table>
        {rows.length === 0 && <Empty>No recommendations match these filters.</Empty>}
      </div>
    </Card>
  );
}
