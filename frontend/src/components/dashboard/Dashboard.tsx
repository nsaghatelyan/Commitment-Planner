"use client";

import { Loader2, RefreshCw } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ThemeToggle } from "@/components/ThemeToggle";
import { Button, Callout, Segmented, Skeleton, Tabs } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { RISK_PROFILES, type AnalysisRun, type Commitment, type DailyUsage, type Recommendation, type RiskProfile, type TenantSummary } from "@/lib/types";

import { BacktestPanel } from "./BacktestPanel";
import { CommitmentsPanel } from "./CommitmentsPanel";
import { ExcludedPanel } from "./ExcludedPanel";
import { NativeComparison } from "./NativeComparison";
import { Overview } from "./Overview";
import { PlanTable } from "./PlanTable";
import { RecommendationDetail } from "./RecommendationDetail";

type Tab = "overview" | "plan" | "native" | "commitments" | "excluded" | "backtest";

function cloudsLabel(t: TenantSummary) {
  return t.connections
    .map((c) => (c.provider === "aws" ? "AWS" : `Azure${c.agreement_type ? ` ${c.agreement_type.toUpperCase()}` : ""}`))
    .join(" + ");
}

export function Dashboard({ tenantId }: { tenantId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const risk = (params.get("risk") as RiskProfile) || "balanced";
  const tab = (params.get("tab") as Tab) || "overview";
  const recId = params.get("rec");

  const [tenants, setTenants] = useState<TenantSummary[]>([]);
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [daily, setDaily] = useState<DailyUsage | null>(null);
  const [commitments, setCommitments] = useState<Commitment[] | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "running" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const setParam = useCallback(
    (changes: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(changes)) {
        if (v === null) next.delete(k);
        else next.set(k, v);
      }
      router.replace(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [params, pathname, router],
  );

  useEffect(() => {
    api.tenants().then(setTenants).catch(() => setTenants([]));
  }, []);

  useEffect(() => {
    setDaily(null);
    setCommitments(null);
    api.dailyUsage(tenantId).then(setDaily).catch(() => setDaily({ as_of: null, days: [] }));
    api.commitments(tenantId).then(setCommitments).catch(() => setCommitments([]));
  }, [tenantId]);

  const loadRun = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const r = await api.latestRun(tenantId, risk);
      setRun(r);
      setRecs(await api.recommendations(tenantId, r.id));
      setState("ready");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setRun(null);
        setRecs([]);
        setState("missing");
      } else {
        setError(String((e as Error).message));
        setState("error");
      }
    }
  }, [tenantId, risk]);

  useEffect(() => {
    loadRun();
  }, [loadRun]);

  const rerun = async () => {
    setState("running");
    try {
      await api.runAnalysis(tenantId, risk);
      await loadRun();
    } catch (e) {
      setError(String((e as Error).message));
      setState("error");
    }
  };

  const tenant = tenants.find((t) => t.id === tenantId);
  const selected = useMemo(() => recs.find((r) => r.id === recId) ?? null, [recs, recId]);
  const summary = run?.summary;
  const plan = recs.filter((r) => r.source === "engine" && r.plan_rank !== null);

  const onStatus = async (r: Recommendation, status: Recommendation["status"]) => {
    await api.setStatus(r.id, status);
    setRecs((all) => all.map((x) => (x.id === r.id ? { ...x, status } : x)));
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-border bg-surface/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-3 px-6 py-3">
          <div className="mr-2 text-[15px] font-semibold tracking-tight">Commitment Planner</div>
          <label className="flex items-center gap-2 text-[12px] text-text-2">
            Client
            <select
              aria-label="Client"
              value={tenantId}
              onChange={(e) => router.push(`/t/${e.target.value}?risk=${risk}&tab=${tab}`)}
              className="h-8 rounded-lg border border-border bg-surface px-2 text-[13px] text-text outline-none focus:border-accent"
            >
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} — {cloudsLabel(t)}
                </option>
              ))}
              {!tenant && <option value={tenantId}>Loading…</option>}
            </select>
          </label>
          <div className="flex items-center gap-2 text-[12px] text-text-2">
            Risk profile
            <Segmented
              label="Risk profile"
              value={risk}
              onChange={(v) => setParam({ risk: v, rec: null })}
              options={RISK_PROFILES.map((p) => ({ value: p, label: p }))}
            />
          </div>
          <div className="ml-auto flex items-center gap-2">
            {summary && (
              <span className="text-[12px] text-muted">
                Data through {summary.as_of} · {summary.history_days} days of history
              </span>
            )}
            <Button size="sm" onClick={rerun} disabled={state === "running"} title="Re-run the analysis for this profile">
              {state === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              Re-run
            </Button>
            <ThemeToggle />
          </div>
        </div>
        <div className="mx-auto max-w-[1440px] px-6">
          <Tabs
            value={tab}
            onChange={(v) => setParam({ tab: v })}
            tabs={[
              { value: "overview", label: "Overview" },
              { value: "plan", label: "Purchase plan", count: plan.length },
              { value: "native", label: "Engine vs native" },
              { value: "commitments", label: "Existing commitments", count: commitments?.length },
              { value: "excluded", label: "Not committed", count: summary?.skipped_pools.length },
              { value: "backtest", label: "Backtest" },
            ]}
          />
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-6 py-6">
        {state === "loading" && (
          <div className="grid gap-3 md:grid-cols-3">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-64 md:col-span-3" />
          </div>
        )}
        {state === "running" && <Callout title="Running the analysis…">Simulating every hour of usage for each commitment pool. This takes a few seconds.</Callout>}
        {state === "error" && <Callout tone="critical" title="Something went wrong">{error}</Callout>}
        {state === "missing" && (
          <Callout title={`No ${risk} analysis yet`}>
            <span className="flex items-center gap-3">
              Run the engine for this client with the {risk} profile.
              <Button variant="primary" size="sm" onClick={rerun}>
                Run analysis
              </Button>
            </span>
          </Callout>
        )}
        {state === "ready" && summary && (
          <>
            {tab === "overview" && <Overview summary={summary} daily={daily} onOpenPlan={() => setParam({ tab: "plan" })} />}
            {tab === "plan" && (
              <div className="space-y-4">
                {summary.expiring_commitments.some((e) => e.urgent) && (
                  <Callout tone="critical" title="Urgent renewals">
                    {summary.expiring_commitments
                      .filter((e) => e.urgent)
                      .map((e) => `${e.instance_type ?? e.kind} ends ${e.end} (${e.days_left} days)`)
                      .join(" · ")}
                  </Callout>
                )}
                {summary.underutilized_commitments.length > 0 && (
                  <Callout tone="warning" title="Stranded or underutilized commitments">
                    {summary.underutilized_commitments
                      .map((u) => `${u.instance_type ?? u.kind}: ${u.status}, ${u.utilization_pct?.toFixed(0)}% utilized`)
                      .join(" · ")}
                  </Callout>
                )}
                <PlanTable recommendations={recs} tenantName={tenant?.name ?? "client"} onSelect={(r) => setParam({ rec: r.id })} />
                {recs.some((r) => r.action === "flag") && (
                  <PlanTable
                    recommendations={recs.filter((r) => r.action === "flag").map((r, i) => ({ ...r, plan_rank: i + 1 }))}
                    tenantName={`${tenant?.name ?? "client"}-review`}
                    onSelect={(r) => setParam({ rec: r.id })}
                  />
                )}
              </div>
            )}
            {tab === "native" && <NativeComparison summary={summary} recommendations={recs} />}
            {tab === "commitments" && <CommitmentsPanel commitments={commitments} summary={summary} />}
            {tab === "excluded" && <ExcludedPanel summary={summary} />}
            {tab === "backtest" && <BacktestPanel backtest={summary.backtest} />}
          </>
        )}
      </main>

      <RecommendationDetail rec={selected} activeProfile={risk} onClose={() => setParam({ rec: null })} onStatus={onStatus} />
    </div>
  );
}
