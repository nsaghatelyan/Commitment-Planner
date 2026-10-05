"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Segmented } from "@/components/ui";
import { money } from "@/lib/format";
import type { ServiceSpend } from "@/lib/types";

import { AXIS, GRID, Legend, TooltipBox } from "./common";

const SERIES = [
  { key: "committed", label: "Commitment-covered", color: "var(--c-committed)" },
  { key: "on_demand", label: "On-demand", color: "var(--c-ondemand)" },
  { key: "spot", label: "Spot", color: "var(--c-spot)" },
] as const;

const SHORT: Record<string, string> = {
  "Amazon Elastic Compute Cloud - Compute": "EC2",
  "Amazon Relational Database Service": "RDS",
  "Amazon ElastiCache": "ElastiCache",
  "Amazon Elastic Container Service": "Fargate (ECS)",
  "Azure Database for PostgreSQL": "Azure PostgreSQL",
};

export function SpendByService({ rows }: { rows: ServiceSpend[] }) {
  const [view, setView] = useState<"service" | "cloud">("service");
  const [asTable, setAsTable] = useState(false);
  const data =
    view === "service"
      ? rows.slice(0, 10).map((r) => ({ ...r, name: `${SHORT[r.service] ?? r.service} · ${r.provider.toUpperCase()}` }))
      : Object.values(
          rows.reduce<Record<string, { name: string; committed: number; on_demand: number; spot: number }>>((acc, r) => {
            const k = r.provider.toUpperCase();
            acc[k] ??= { name: k, committed: 0, on_demand: 0, spot: 0 };
            acc[k].committed += r.committed;
            acc[k].on_demand += r.on_demand;
            acc[k].spot += r.spot;
            return acc;
          }, {}),
        );
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Segmented
          label="Group spend by"
          value={view}
          onChange={setView}
          options={[
            { value: "service", label: "By service" },
            { value: "cloud", label: "By cloud" },
          ]}
        />
        <button className="text-[12px] text-accent hover:underline" onClick={() => setAsTable((t) => !t)}>
          {asTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {asTable ? (
        <table className="w-full text-[12px] tabular">
          <thead className="text-left text-muted">
            <tr>
              <th className="py-1 font-medium">{view === "service" ? "Service" : "Cloud"}</th>
              {SERIES.map((s) => (
                <th key={s.key} className="py-1 text-right font-medium">
                  {s.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.name} className="border-t border-border">
                <td className="py-1.5">{r.name}</td>
                {SERIES.map((s) => (
                  <td key={s.key} className="py-1.5 text-right">
                    {money(r[s.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div style={{ height: Math.max(120, data.length * 30 + 30) }} role="img" aria-label="Monthly spend split by service">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 0, right: 12, bottom: 0, left: 0 }} barCategoryGap={6}>
              <CartesianGrid {...GRID} horizontal={false} vertical />
              <XAxis type="number" {...AXIS} tickFormatter={(v) => money(v, { compact: true })} />
              <YAxis type="category" dataKey="name" {...AXIS} axisLine={false} width={150} />
              <Tooltip
                cursor={{ fill: "var(--surface-2)" }}
                content={({ active, payload, label }) =>
                  active && payload?.length ? (
                    <TooltipBox
                      title={`${label} · per month`}
                      rows={SERIES.map((s) => ({
                        label: s.label,
                        value: money(Number(payload[0].payload[s.key])),
                        color: s.color,
                      }))}
                    />
                  ) : null
                }
              />
              {SERIES.map((s, i) => (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  stackId="a"
                  fill={s.color}
                  stroke="var(--surface)"
                  strokeWidth={1}
                  radius={i === SERIES.length - 1 ? [0, 3, 3, 0] : 0}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className="mt-2">
        <Legend items={SERIES.map((s) => ({ label: s.label, color: s.color }))} />
      </div>
    </div>
  );
}
