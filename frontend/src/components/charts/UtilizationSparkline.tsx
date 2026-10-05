"use client";

import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from "recharts";

import { pct, shortDate } from "@/lib/format";

import { TooltipBox } from "./common";

export function UtilizationSparkline({ points }: { points: { date: string; utilization_pct: number }[] }) {
  if (!points.length) return <span className="text-[12px] text-muted">no data</span>;
  return (
    <div className="h-9 w-[220px]" role="img" aria-label="Daily utilization">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 3, right: 2, bottom: 3, left: 2 }}>
          <YAxis domain={[0, 100]} hide />
          <ReferenceLine y={80} stroke="var(--grid)" strokeDasharray="3 3" />
          <Tooltip
            content={({ active, payload }) =>
              active && payload?.length ? (
                <TooltipBox
                  title={shortDate(payload[0].payload.date)}
                  rows={[{ label: "Utilization", value: pct(payload[0].payload.utilization_pct) }]}
                />
              ) : null
            }
          />
          <Line type="monotone" dataKey="utilization_pct" stroke="var(--c-committed)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
