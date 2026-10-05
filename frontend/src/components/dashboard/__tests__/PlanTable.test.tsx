import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { rec } from "@/test/fixtures";

import { PlanTable } from "../PlanTable";

const recs = [
  rec({ id: "a", plan_rank: 1, monthly_savings: 100, provider: "aws" }),
  rec({ id: "b", plan_rank: 2, monthly_savings: 900, provider: "azure", kind: "azure_ri", instance_type: "Standard_D4s_v5", service: "Virtual Machines" }),
  rec({ id: "c", plan_rank: 3, monthly_savings: 50, kind: "aws_sp_compute", hourly_commitment: 1.5, quantity: null, action: "renew", urgent: true, service: "compute", region: null }),
  rec({ id: "n", plan_rank: null, source: "native", monthly_savings: 5000 }),
];

function rows() {
  return screen.getAllByRole("row").slice(1, -1); // without header and totals
}

describe("PlanTable", () => {
  it("lists engine plan rows in rank order with totals and badges", () => {
    render(<PlanTable recommendations={recs} tenantName="Acme" onSelect={() => {}} />);
    expect(rows()).toHaveLength(3);
    expect(within(rows()[2]).getByText("$1.500/h")).toBeInTheDocument();
    expect(within(rows()[2]).getByText("Urgent")).toBeInTheDocument();
    expect(screen.getByText("$1,050")).toBeInTheDocument(); // total savings, native excluded
  });

  it("filters by cloud and type, sorts by savings, and opens a row", () => {
    const onSelect = vi.fn();
    render(<PlanTable recommendations={recs} tenantName="Acme" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("radio", { name: "Azure" }));
    expect(rows()).toHaveLength(1);
    fireEvent.click(screen.getByRole("radio", { name: "All clouds" }));
    fireEvent.click(screen.getByRole("radio", { name: "Savings plans" }));
    expect(rows()).toHaveLength(1);
    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    fireEvent.click(screen.getByRole("button", { name: /Sort by Savings/ }));
    expect(within(rows()[0]).getByText("$900")).toBeInTheDocument();
    fireEvent.click(rows()[0]);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "b" }));
  });
});
