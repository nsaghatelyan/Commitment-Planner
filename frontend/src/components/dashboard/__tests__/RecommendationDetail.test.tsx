import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { rec } from "@/test/fixtures";

import { RecommendationDetail } from "../RecommendationDetail";
import { BacktestPanel } from "../BacktestPanel";

describe("RecommendationDetail", () => {
  it("shows the rationale, stability, profiles and term options", () => {
    const onStatus = vi.fn();
    const r = rec();
    render(<RecommendationDetail rec={r} activeProfile="balanced" onClose={() => {}} onStatus={onStatus} />);
    expect(screen.getByText(/never dropped below 16/)).toBeInTheDocument();
    expect(screen.getByText("Hourly baseline vs. the commitment")).toBeInTheDocument();
    expect(screen.getByText("2026-08-01 (50%)")).toBeInTheDocument(); // step change
    expect(screen.getByText("recommended")).toBeInTheDocument();
    expect(screen.getByText("current")).toBeInTheDocument();
    expect(screen.getByText("month 13")).toBeInTheDocument(); // 3y all upfront break-even
    fireEvent.click(screen.getByRole("button", { name: /Accept/ }));
    expect(onStatus).toHaveBeenCalledWith(r, "accepted");
  });

  it("renders nothing without a recommendation", () => {
    const { container } = render(<RecommendationDetail rec={null} activeProfile="balanced" onClose={() => {}} onStatus={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("BacktestPanel", () => {
  it("explains when a backtest is not available", () => {
    render(<BacktestPanel backtest={{ available: false, reason: "needs at least 30 days of history" }} />);
    expect(screen.getByText(/needs at least 30 days/)).toBeInTheDocument();
  });

  it("shows projected vs realized", () => {
    render(
      <BacktestPanel
        backtest={{
          available: true, train_until: "2026-09-01", holdout_days: 30, projected_monthly_savings: 1000,
          realized_monthly_savings: 940, savings_accuracy_pct: 94, projected_utilization_pct: 99, realized_utilization_pct: 96,
          recommendations: [{ plan_rank: 1, action: "purchase", kind: "aws_ri", pool: "aws_ri / ec2 / us-east-1 / m5 / Linux",
            instance_type: "m5.large", quantity: 2, hourly_commitment: null, projected_utilization_pct: 100,
            realized_utilization_pct: 85, projected_monthly_savings: 100, realized_monthly_savings: 80 }],
        }}
      />,
    );
    expect(screen.getByText("94.0%")).toBeInTheDocument();
    expect(screen.getByText("Reserved Instance · EC2 · us-east-1 · m5 · Linux")).toBeInTheDocument();
    expect(screen.getByText("-20.0%")).toBeInTheDocument();
  });
});
