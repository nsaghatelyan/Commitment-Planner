import { kindLabel, payment, term } from "./format";
import type { Recommendation } from "./types";

export const EXPORT_COLUMNS: { header: string; value: (r: Recommendation) => string | number | null }[] = [
  { header: "Rank", value: (r) => r.plan_rank },
  { header: "Action", value: (r) => r.action },
  { header: "Cloud", value: (r) => r.provider.toUpperCase() },
  { header: "Type", value: (r) => kindLabel(r.kind) },
  { header: "Service", value: (r) => r.service },
  { header: "Region", value: (r) => r.region },
  { header: "Instance type / family", value: (r) => r.instance_type ?? r.instance_family },
  { header: "Quantity", value: (r) => r.quantity },
  { header: "Hourly commitment ($/h)", value: (r) => r.hourly_commitment },
  { header: "Term", value: (r) => term(r.term_months) },
  { header: "Payment", value: (r) => payment(r.payment_option) },
  { header: "Upfront ($)", value: (r) => r.upfront_cost },
  { header: "Monthly cost after ($)", value: (r) => r.monthly_cost_after },
  { header: "Monthly savings ($)", value: (r) => r.monthly_savings },
  { header: "Savings %", value: (r) => r.savings_pct },
  { header: "Expected utilization %", value: (r) => r.expected_utilization_pct },
  { header: "Break-even month", value: (r) => r.breakeven_month },
  { header: "Risk", value: (r) => r.risk },
  { header: "Urgent", value: (r) => (r.urgent ? "yes" : "") },
  { header: "Rationale", value: (r) => r.rationale },
];

function csvCell(v: string | number | null): string {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows: Recommendation[]): string {
  const lines = [EXPORT_COLUMNS.map((c) => csvCell(c.header)).join(",")];
  for (const r of rows) lines.push(EXPORT_COLUMNS.map((c) => csvCell(c.value(r))).join(","));
  return lines.join("\n") + "\n";
}

function download(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}

export function exportCsv(rows: Recommendation[], fileName: string) {
  download(new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" }), fileName);
}

export async function exportXlsx(rows: Recommendation[], fileName: string) {
  const { default: writeExcelFile } = await import("write-excel-file/browser");
  const header = EXPORT_COLUMNS.map((c) => ({ value: c.header, fontWeight: "bold" as const }));
  const body = rows.map((r) =>
    EXPORT_COLUMNS.map((c) => {
      const v = c.value(r);
      return v === null || v === undefined ? null : { value: v };
    }),
  );
  await writeExcelFile([header, ...body]).toFile(fileName);
}
