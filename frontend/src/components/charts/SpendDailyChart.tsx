"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { money, shortDate } from "@/lib/format";
import type { DailyUsage } from "@/lib/types";

import { AXIS, GRID, Legend, TooltipBox } from "./common";

const SERIES = [
  { key: "committed", label: "Covered by commitments", color: "var(--c-committed)" },
  { key: "on_demand", label: "On-demand", color: "var(--c-ondemand)" },
  { key: "spot", label: "Spot", color: "var(--c-spot)" },
  { key: "unused", label: "Unused commitment", color: "var(--critical)" },
] as const;

export function SpendDailyChart({ data }: { data: DailyUsage["days"] }) {
  return (
    <div>
      <div className="h-[220px]" role="img" aria-label="Daily cost of commitment-eligible usage by how it was paid">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="date" {...AXIS} tickFormatter={shortDate} minTickGap={40} />
            <YAxis {...AXIS} axisLine={false} width={56} tickFormatter={(v) => money(v, { compact: true })} />
            <Tooltip
              cursor={{ fill: "var(--surface-2)" }}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <TooltipBox
                    title={shortDate(String(label))}
                    rows={[
                      ...SERIES.map((s) => ({
                        label: s.label,
                        value: money(Number(payload[0].payload[s.key])),
                        color: s.color,
                      })),
                    ]}
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
                strokeWidth={0.5}
                radius={i === SERIES.length - 1 ? [2, 2, 0, 0] : 0}
                isAnimationActive={false}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2">
        <Legend items={SERIES.map((s) => ({ label: s.label, color: s.color }))} />
      </div>
    </div>
  );
}
