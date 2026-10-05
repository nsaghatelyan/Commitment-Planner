"use client";

import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { num, shortDate } from "@/lib/format";
import type { ChartData } from "@/lib/types";

import { AXIS, GRID, Legend, TooltipBox } from "./common";

function addDays(iso: string, n: number) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Daily min / mean / max over the whole history, the commitment line, and where sizing
 * starts (after a step change, a ramp, or the lookback limit). */
export function HistoryChart({
  chart,
  unit,
  stepDay,
  sizingReason,
}: {
  chart: ChartData;
  unit: string;
  stepDay: string | null;
  sizingReason: string | null;
}) {
  const fmt = (v: number) => (unit.startsWith("$") ? `$${v.toFixed(2)}` : num(v, 1));
  const data = chart.daily_mean.map((mean, i) => ({
    date: addDays(chart.daily_start, i),
    band: [chart.daily_min[i], chart.daily_max[i]],
    min: chart.daily_min[i],
    max: chart.daily_max[i],
    mean,
  }));
  const last = data.at(-1)?.date;
  const reasonLabel =
    sizingReason === "step" ? "Sized after step change" : sizingReason === "ramp" ? "Ramp: recent floor" : "Sizing window";
  return (
    <div>
      <div className="h-[220px]" role="img" aria-label="Daily usage range over the full history">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 18, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="date" {...AXIS} tickFormatter={shortDate} minTickGap={40} />
            <YAxis {...AXIS} axisLine={false} width={56} tickFormatter={fmt} />
            {last && chart.sizing_from <= last && (
              <ReferenceArea
                x1={chart.sizing_from < data[0].date ? data[0].date : chart.sizing_from}
                x2={last}
                fill="var(--accent)"
                fillOpacity={0.06}
                label={{ value: reasonLabel, position: "insideTopLeft", fill: "var(--text-2)", fontSize: 11 }}
              />
            )}
            <Tooltip
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <TooltipBox
                    title={shortDate(String(label))}
                    rows={[
                      { label: "Max", value: fmt(payload[0].payload.max) },
                      { label: "Mean", value: fmt(payload[0].payload.mean), color: "var(--c-committed)" },
                      { label: "Min (floor)", value: fmt(payload[0].payload.min) },
                      { label: "Commitment", value: fmt(chart.commitment_line), color: "var(--text)", dashed: true },
                    ]}
                  />
                ) : null
              }
            />
            <Area type="monotone" dataKey="band" stroke="none" fill="var(--c-committed)" fillOpacity={0.16} isAnimationActive={false} />
            <Line type="monotone" dataKey="mean" stroke="var(--c-committed)" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="min" stroke="var(--c-committed)" strokeWidth={1} strokeOpacity={0.6} dot={false} isAnimationActive={false} />
            <ReferenceLine y={chart.commitment_line} stroke="var(--text)" strokeWidth={1.5} strokeDasharray="5 4" />
            {stepDay && (
              <ReferenceLine
                x={stepDay}
                stroke="var(--serious)"
                strokeWidth={2}
                label={{ value: "Step change", position: "top", fill: "var(--text-2)", fontSize: 11 }}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2">
        <Legend
          items={[
            { label: "Daily min–max range", color: "color-mix(in srgb, var(--c-committed) 25%, transparent)" },
            { label: "Daily mean", color: "var(--c-committed)" },
            { label: "Commitment line", color: "var(--text)", dashed: true },
            ...(stepDay ? [{ label: "Step change", color: "var(--serious)" }] : []),
          ]}
        />
      </div>
    </div>
  );
}
