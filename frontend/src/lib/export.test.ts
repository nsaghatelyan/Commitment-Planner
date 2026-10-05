import { describe, expect, it } from "vitest";

import { rec } from "@/test/fixtures";

import { EXPORT_COLUMNS, toCsv } from "./export";

describe("CSV export", () => {
  it("writes a header and one row per recommendation, escaping text", () => {
    const csv = toCsv([rec({ rationale: 'Floor is "steady", never below 16' })]);
    const [header, row] = csv.trim().split("\n");
    expect(header.split(",")).toHaveLength(EXPORT_COLUMNS.length);
    expect(header).toContain("Monthly savings ($)");
    expect(row).toContain("Reserved Instance");
    expect(row).toContain('"Floor is ""steady"", never below 16"');
  });
});
