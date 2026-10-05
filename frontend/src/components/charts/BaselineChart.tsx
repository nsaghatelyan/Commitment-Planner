"use client";

import { Area, CartesianGrid, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { num } from "@/lib/format";
import type { ChartData } from "@/lib/types";

import { AXIS, GRID, Legend, TooltipBox } from "./common";

function fmtUnit(v: number, unit: string) {
  return unit.startsWith("$") ? `$${v.toFixed(2)}` : num(v, 1);
}

/** Hourly baseline for the last 30 days with the commitment line: covered usage, usage left
 * on demand above the line, and unused commitment below it. */
export function BaselineChart({ chart, unit }: { chart: ChartData; unit: string }) {
  const line = chart.commitment_line;
  const start = new Date(chart.hourly_start).getTime();
  const data = chart.hourly.map((v, i) => ({
    t: start + i * 3600_000,
    usage: v,
    covered: Math.min(v, line),
    over: Math.max(v - line, 0),
    unused: v < line ? [v, line] : [line, line],
  }));
  const hourLabel = (t: number) =>
    new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", timeZone: "UTC" });
  return (
    <div>
      <div className="h-[240px]" role="img" aria-label="Hourly usage against the recommended commitment">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 10, right: 12, bottom: 0, left: 0 }}>
            <defs>
              <pattern id="unusedHatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="5" height="5" fill="var(--critical)" fillOpacity={0.08} />
                <line x1="0" y1="0" x2="0" y2="5" stroke="var(--critical)" strokeWidth={1.5} strokeOpacity={0.55} />
              </pattern>
            </defs>
            <CartesianGrid {...GRID} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              {...AXIS}
              tickFormatter={(t) => new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}
              minTickGap={50}
            />
            <YAxis {...AXIS} axisLine={false} width={56} tickFormatter={(v) => fmtUnit(v, unit)} />
            <Tooltip
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload;
                return (
                  <TooltipBox
                    title={hourLabel(p.t)}
                    rows={[
                      { label: "Usage", value: fmtUnit(p.usage, unit) },
                      { label: "Covered", value: fmtUnit(p.covered, unit), color: "var(--c-committed)" },
                      { label: "On-demand above line", value: fmtUnit(p.over, unit), color: "var(--c-ondemand)" },
                      { label: "Unused commitment", value: fmtUnit(Math.max(line - p.usage, 0), unit), color: "var(--critical)" },
                    ]}
                  />
                );
              }}
            />
            <Area type="stepAfter" dataKey="covered" stackId="u" stroke="none" fill="var(--c-committed)" fillOpacity={0.75} isAnimationActive={false} />
            <Area type="stepAfter" dataKey="over" stackId="u" stroke="none" fill="var(--c-ondemand)" fillOpacity={0.55} isAnimationActive={false} />
            <Area type="stepAfter" dataKey="unused" stroke="none" fill="url(#unusedHatch)" isAnimationActive={false} />
            <ReferenceLine
              y={line}
              stroke="var(--text)"
              strokeWidth={1.5}
              strokeDasharray="5 4"
              label={{ value: `Commitment ${fmtUnit(line, unit)}`, position: "insideTopRight", fill: "var(--text-2)", fontSize: 11 }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2">
        <Legend
          items={[
            { label: "Covered by the commitment", color: "var(--c-committed)" },
            { label: "Left on demand", color: "var(--c-ondemand)" },
            { label: "Unused commitment", color: "var(--critical)", hatched: true },
            { label: "Commitment line", color: "var(--text)", dashed: true },
          ]}
        />
      </div>
    </div>
  );
}
