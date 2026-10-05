import { describe, expect, it } from "vitest";

import { hourly, kindLabel, money, payment, pct, poolLabel, term } from "./format";

describe("format", () => {
  it("formats money, percentages and terms", () => {
    expect(money(1234.4)).toBe("$1,234");
    expect(money(12.345)).toBe("$12.35");
    expect(money(54321, { compact: true })).toBe("$54.3K");
    expect(money(null)).toBe("—");
    expect(pct(95.123)).toBe("95.1%");
    expect(hourly(0.3)).toBe("$0.300/h");
    expect(term(36)).toBe("3y");
    expect(payment("partial_upfront")).toBe("Partial upfront");
    expect(payment(null)).toBe("Monthly");
  });

  it("labels commitment kinds and pools", () => {
    expect(kindLabel("aws_sp_compute")).toBe("Compute SP");
    expect(poolLabel("aws_ri / ec2 / us-east-1 / m5 / Linux")).toBe("Reserved Instance · EC2 · us-east-1 · m5 · Linux");
  });
});
