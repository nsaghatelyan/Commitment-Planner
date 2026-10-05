"use client";

import type { ReactNode } from "react";

export const AXIS = { stroke: "var(--axis)", tick: { fill: "var(--muted)", fontSize: 11 }, tickLine: false };
export const GRID = { stroke: "var(--grid)", strokeDasharray: "0", vertical: false };

export function TooltipBox({ title, rows }: { title: ReactNode; rows: { label: string; value: string; color?: string; dashed?: boolean }[] }) {
  return (
    <div className="min-w-[180px] rounded-lg border border-border bg-surface px-3 py-2 text-[12px] shadow-lg">
      <div className="mb-1 font-semibold text-text">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4 py-0.5">
          <span className="flex items-center gap-1.5 text-text-2">
            {r.color && (
              <span
                aria-hidden
                className="inline-block h-2 w-2 rounded-sm"
                style={r.dashed ? { border: `1.5px dashed ${r.color}` } : { background: r.color }}
              />
            )}
            {r.label}
          </span>
          <span className="font-medium text-text tabular">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean; hatched?: boolean }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-text-2">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-2.5 w-2.5 rounded-[3px]"
            style={
              i.dashed
                ? { borderTop: `2px dashed ${i.color}`, height: 0, width: 14, borderRadius: 0 }
                : i.hatched
                  ? { background: `repeating-linear-gradient(45deg, ${i.color} 0 2px, transparent 2px 5px)`, border: `1px solid ${i.color}` }
                  : { background: i.color }
            }
          />
          {i.label}
        </li>
      ))}
    </ul>
  );
}
