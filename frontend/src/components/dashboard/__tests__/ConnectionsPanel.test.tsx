import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { Connection } from "@/lib/types";

import { ConnectionsPanel } from "../ConnectionsPanel";

const base: Connection = {
  id: "c1", tenant_id: "t1", provider: "aws", name: "My account", status: "pending",
  last_verified_at: null, last_success_at: null, last_error: null, aws_auth_mode: "role",
  aws_profile: null, aws_role_arn: null, aws_external_id: "svt-abc", aws_export_bucket: null,
  aws_export_prefix: null, azure_tenant_id: null, azure_agreement_type: null,
  azure_billing_scope: null, azure_export_container: null, azure_client_id: null,
  azure_credential_ref: null,
  deploy_command: "aws cloudformation deploy ... ExternalId=svt-abc", runs: [],
};

afterEach(() => vi.restoreAllMocks());

describe("ConnectionsPanel", () => {
  it("shows the role deploy steps and blocks testing until the ARN is set", async () => {
    vi.spyOn(api, "connections").mockResolvedValue([base]);
    render(<ConnectionsPanel tenantId="t1" onAnalysisReady={() => {}} />);
    expect(await screen.findByText(/ExternalId=svt-abc/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Test connection/ })).toBeDisabled();
    expect(screen.getByText(/costs \$0\.01/)).toBeInTheDocument();
  });

  it("tests a profile connection and shows the error message", async () => {
    const conn = { ...base, aws_auth_mode: "profile" as const, aws_profile: "default", deploy_command: undefined };
    vi.spyOn(api, "connections").mockResolvedValue([conn]);
    const test = vi.spyOn(api, "testConnection").mockResolvedValue({
      ok: false, message: "Access denied for ce:GetCostAndUsage.",
    });
    render(<ConnectionsPanel tenantId="t1" onAnalysisReady={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /Test connection/ }));
    await waitFor(() => expect(test).toHaveBeenCalledWith("c1"));
    expect(await screen.findByText("Access denied for ce:GetCostAndUsage.")).toBeInTheDocument();
  });

  it("shows collection runs with Cost Explorer call counts and warnings", async () => {
    vi.spyOn(api, "connections").mockResolvedValue([{
      ...base, aws_auth_mode: "profile", status: "active",
      runs: [{
        id: "r1", status: "succeeded", created_at: "2026-10-05T10:00:00Z", started_at: "2026-10-05T10:00:00Z",
        finished_at: "2026-10-05T10:02:00Z", error: null, rows_ingested: 1234, api_calls: 80,
        cost_explorer_calls: 47, cost_explorer_usd: 0.47, period_start: "2025-09-01", period_end: "2026-10-04",
        sources: ["api_bootstrap"], warnings: ["memorydb:DescribeReservedNodes (ap-southeast-4): not offered"],
        prices_synced: 42,
      }],
    }]);
    render(<ConnectionsPanel tenantId="t1" onAnalysisReady={() => {}} />);
    expect(await screen.findByText("47 calls · $0.47")).toBeInTheDocument();
    expect(screen.getByText("1 warning(s)")).toBeInTheDocument();
    expect(screen.getByText("Cost Explorer backfill")).toBeInTheDocument();
  });
});
