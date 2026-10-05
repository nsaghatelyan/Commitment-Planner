"use client";

import { CheckCircle2, CloudDownload, Download, Loader2, Plug, Plus, Trash2, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, Button, Callout, Card, CardHeader, Empty, Segmented } from "@/components/ui";
import { CodeBlock, Field, Select, TextInput } from "@/components/ui/Field";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import type { CollectionRunInfo, Connection, ConnectionInput, ConnectionTestResult } from "@/lib/types";

const STATUS_TONE: Record<string, "good" | "warning" | "critical" | "neutral" | "accent"> = {
  active: "good",
  pending: "warning",
  error: "critical",
  synthetic: "accent",
  queued: "neutral",
  running: "accent",
  succeeded: "good",
  failed: "critical",
};

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function duration(r: CollectionRunInfo) {
  if (!r.started_at) return "—";
  const end = r.finished_at ? new Date(r.finished_at) : new Date();
  const s = Math.round((end.getTime() - new Date(r.started_at).getTime()) / 1000);
  return s < 90 ? `${s}s` : `${Math.round(s / 60)}m`;
}

function CostNote() {
  return (
    <p className="text-[12px] text-muted">
      Each Cost Explorer API call costs $0.01. A first collection backfills 13 months and makes roughly 50–100 calls
      (≈ $0.50–$1); responses are cached for 24 hours, so a re-run soon after is mostly free.
    </p>
  );
}

function AddConnection({ tenantId, onCreated }: { tenantId: string; onCreated: () => void }) {
  const [provider, setProvider] = useState<"aws" | "azure">("aws");
  const [mode, setMode] = useState<"profile" | "role">("profile");
  const [form, setForm] = useState<Record<string, string>>({ name: "", aws_profile: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: ConnectionInput =
      provider === "aws"
        ? {
            provider,
            name: form.name,
            aws_auth_mode: mode,
            aws_profile: mode === "profile" ? form.aws_profile?.trim() || null : null,
            aws_role_arn: mode === "role" ? form.aws_role_arn || null : null,
            aws_export_bucket: form.aws_export_bucket || null,
            aws_export_prefix: form.aws_export_prefix || null,
          }
        : {
            provider,
            name: form.name,
            azure_tenant_id: form.azure_tenant_id,
            azure_agreement_type: form.azure_agreement_type || "payg",
            azure_billing_scope: form.azure_billing_scope,
            azure_export_container: form.azure_export_container || null,
            azure_client_id: form.azure_client_id || null,
            azure_credential_ref: form.azure_credential_ref || null,
          };
    try {
      await api.createConnection(tenantId, body);
      setForm({ name: "", aws_profile: "" });
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader title="Add a connection" description="Read-only access to cost and commitment data. No secrets are stored." />
      <form onSubmit={submit} className="space-y-4 px-5 pb-5">
        <div className="flex flex-wrap gap-3">
          <Segmented
            label="Cloud"
            value={provider}
            onChange={setProvider}
            options={[
              { value: "aws", label: "AWS" },
              { value: "azure", label: "Azure" },
            ]}
          />
          {provider === "aws" && (
            <Segmented
              label="Access"
              value={mode}
              onChange={setMode}
              options={[
                { value: "profile", label: "Local AWS profile" },
                { value: "role", label: "Cross-account role" },
              ]}
            />
          )}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Name">
            <TextInput required value={form.name} onChange={set("name")} placeholder="e.g. My account" />
          </Field>
          {provider === "aws" && mode === "profile" && (
            <Field
              label="Credentials profile (optional)"
              hint="Leave empty to use the [default] profile in ~/.aws/credentials (the default credential chain)."
            >
              <TextInput value={form.aws_profile ?? ""} onChange={set("aws_profile")} placeholder="default" />
            </Field>
          )}
          {provider === "aws" && mode === "role" && (
            <Field label="Role ARN (optional now)" hint="You get it after deploying the CloudFormation stack shown on the next step.">
              <TextInput value={form.aws_role_arn ?? ""} onChange={set("aws_role_arn")} placeholder="arn:aws:iam::123456789012:role/SavingsToolReadOnly" />
            </Field>
          )}
          {provider === "aws" && (
            <>
              <Field label="Data Export bucket (optional)" hint="FOCUS 1.0 or CUR 2.0 Parquet. Without it, history comes from Cost Explorer.">
                <TextInput value={form.aws_export_bucket ?? ""} onChange={set("aws_export_bucket")} />
              </Field>
              <Field label="Data Export prefix (optional)">
                <TextInput value={form.aws_export_prefix ?? ""} onChange={set("aws_export_prefix")} />
              </Field>
            </>
          )}
          {provider === "azure" && (
            <>
              <Field label="Tenant ID">
                <TextInput required value={form.azure_tenant_id ?? ""} onChange={set("azure_tenant_id")} />
              </Field>
              <Field label="Agreement type">
                <Select value={form.azure_agreement_type ?? "payg"} onChange={set("azure_agreement_type")}>
                  <option value="payg">Pay-as-you-go</option>
                  <option value="ea">Enterprise Agreement</option>
                  <option value="mca">Microsoft Customer Agreement</option>
                  <option value="csp">CSP</option>
                </Select>
              </Field>
              <Field label="Billing scope" hint="PAYG: /subscriptions/<id> · EA: /providers/Microsoft.Billing/billingAccounts/<enrollment> · MCA: …/billingProfiles/<id>">
                <TextInput required value={form.azure_billing_scope ?? ""} onChange={set("azure_billing_scope")} />
              </Field>
              <Field label="FOCUS export container (optional)" hint="https://<account>.blob.core.windows.net/<container>/<prefix>">
                <TextInput value={form.azure_export_container ?? ""} onChange={set("azure_export_container")} />
              </Field>
              <Field label="App (client) ID (optional)" hint="Defaults to the tool's own Entra app.">
                <TextInput value={form.azure_client_id ?? ""} onChange={set("azure_client_id")} />
              </Field>
              <Field label="Credential reference (optional)" hint='A certificate path, or "env:VAR" naming an environment variable with a client secret.'>
                <TextInput value={form.azure_credential_ref ?? ""} onChange={set("azure_credential_ref")} />
              </Field>
            </>
          )}
        </div>
        {provider === "aws" && mode === "profile" && <ConsoleCredentialsHelp />}
        {error && <p className="text-[12px] text-critical">{error}</p>}
        <Button type="submit" variant="primary" disabled={busy || !form.name.trim()}>
          <Plus className="h-3.5 w-3.5" /> Add connection
        </Button>
      </form>
    </Card>
  );
}

const READ_ONLY_POLICY = JSON.stringify(
  {
    Version: "2012-10-17",
    Statement: [
      {
        Effect: "Allow",
        Action: [
          "ce:GetCostAndUsage", "ce:GetDimensionValues", "ce:GetReservationUtilization",
          "ce:GetReservationPurchaseRecommendation", "ce:GetSavingsPlansUtilizationDetails",
          "ce:GetSavingsPlansPurchaseRecommendation", "savingsplans:DescribeSavingsPlans",
          "savingsplans:DescribeSavingsPlansOfferingRates", "ec2:DescribeRegions",
          "ec2:DescribeReservedInstances", "rds:DescribeReservedDBInstances",
          "elasticache:DescribeReservedCacheNodes", "redshift:DescribeReservedNodes",
          "es:DescribeReservedInstances", "memorydb:DescribeReservedNodes",
          "organizations:DescribeOrganization", "organizations:ListAccounts", "pricing:GetProducts",
          "s3:ListBucket", "s3:GetObject",
        ],
        Resource: "*",
      },
    ],
  },
  null,
  2,
);

function ConsoleCredentialsHelp() {
  return (
    <details className="rounded-lg border border-border p-3 text-[12.5px]" open>
      <summary className="cursor-pointer font-semibold text-text">Set up credentials in the AWS console (no AWS CLI needed)</summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-text-2">
        <li>
          <b>IAM → Users → Create user</b> (e.g. <code>savings-tool-test</code>), without console access.
        </li>
        <li>
          <b>Attach policies directly → Create policy → JSON</b>: paste this read-only policy, save it, and attach it to the
          user.
          <details className="mt-1">
            <summary className="cursor-pointer text-accent">Show policy</summary>
            <div className="mt-1">
              <CodeBlock code={READ_ONLY_POLICY} />
            </div>
          </details>
        </li>
        <li>
          Open the user → <b>Security credentials → Create access key</b> → use case &ldquo;Application running outside AWS&rdquo;.
          Copy both keys (the secret is shown once).
        </li>
        <li>
          On the machine running the tool, create the text file <code>~/.aws/credentials</code> with:
          <CodeBlock code={"[default]\naws_access_key_id = <Access key ID>\naws_secret_access_key = <Secret access key>"} />
          Use a different section name (e.g. <code>[savings-tool]</code>) and enter it above if you already have a default.
        </li>
        <li>
          Make sure <b>Billing and Cost Management → Cost Explorer</b> is enabled (new accounts need up to 24 hours for data).
        </li>
        <li>Restart the worker so it picks up the file. Keys are never stored by the tool; delete the access key when you finish testing.</li>
      </ol>
    </details>
  );
}

function RunsTable({ runs }: { runs: CollectionRunInfo[] }) {
  if (!runs.length) return <p className="text-[12px] text-muted">No collections yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-[12.5px] tabular">
        <thead className="text-left text-[12px] text-muted">
          <tr>
            <th className="py-1.5 font-medium">Status</th>
            <th className="py-1.5 font-medium">Started</th>
            <th className="py-1.5 font-medium">Took</th>
            <th className="py-1.5 font-medium">Data</th>
            <th className="py-1.5 text-right font-medium">Rows</th>
            <th className="py-1.5 text-right font-medium">API calls</th>
            <th className="py-1.5 text-right font-medium">Cost Explorer</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-t border-border align-top">
              <td className="py-2">
                <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>
                  {(r.status === "queued" || r.status === "running") && <Loader2 className="h-3 w-3 animate-spin" />}
                  {r.status}
                </Badge>
                {r.error && <div className="mt-1 max-w-[340px] text-critical">{r.error}</div>}
                {r.warnings.length > 0 && (
                  <details className="mt-1 text-muted">
                    <summary className="cursor-pointer">{r.warnings.length} warning(s)</summary>
                    <ul className="mt-1 max-w-[520px] list-disc space-y-0.5 pl-4">
                      {r.warnings.map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </td>
              <td className="py-2">{when(r.started_at ?? r.created_at)}</td>
              <td className="py-2">{duration(r)}</td>
              <td className="py-2 text-text-2">
                {r.status === "succeeded" ? `${r.period_start} → ${r.period_end}` : "—"}
                {r.sources.length > 0 && <div className="text-[11px] text-muted">{r.sources.join(", ").replace("api_bootstrap", "Cost Explorer backfill")}</div>}
              </td>
              <td className="py-2 text-right">{r.rows_ingested?.toLocaleString() ?? "—"}</td>
              <td className="py-2 text-right">{r.api_calls ?? "—"}</td>
              <td className="py-2 text-right">
                {r.cost_explorer_calls} calls · {money(r.cost_explorer_usd ?? r.cost_explorer_calls * 0.01, { cents: true })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ConnectionCard({ conn, onChange, onCollected }: { conn: Connection; onChange: () => void; onCollected: () => void }) {
  const [test, setTest] = useState<ConnectionTestResult | null>(null);
  const [busy, setBusy] = useState<"test" | "collect" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [arn, setArn] = useState(conn.aws_role_arn ?? "");
  const synthetic = conn.status === "synthetic";
  const role = conn.provider === "aws" && conn.aws_auth_mode === "role" && !synthetic;
  const active = conn.runs.some((r) => r.status === "queued" || r.status === "running");

  const run = async (what: "test" | "collect") => {
    setBusy(what);
    setError(null);
    try {
      if (what === "test") setTest(await api.testConnection(conn.id));
      else {
        await api.collect(conn.id);
        onCollected();
      }
      onChange();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-4">
        <div>
          <div className="flex items-center gap-2 text-[15px] font-semibold">
            <Plug aria-hidden className="h-4 w-4 text-muted" />
            {conn.name}
            <Badge tone={STATUS_TONE[conn.status] ?? "neutral"}>{conn.status}</Badge>
          </div>
          <div className="mt-0.5 text-[12px] text-muted">
            {conn.provider.toUpperCase()} ·{" "}
            {synthetic
              ? "generated demo data"
              : conn.provider === "aws"
              ? role
                ? `cross-account role${conn.aws_role_arn ? ` ${conn.aws_role_arn}` : " (not set yet)"}`
                : conn.aws_profile
                  ? `local profile "${conn.aws_profile}"`
                  : "default credentials on this machine"
              : `${conn.azure_agreement_type?.toUpperCase()} · ${conn.azure_billing_scope}`}
            {conn.aws_export_bucket && ` · export s3://${conn.aws_export_bucket}/${conn.aws_export_prefix ?? ""}`}
          </div>
          <div className="mt-0.5 text-[12px] text-muted">
            Last verified {when(conn.last_verified_at)} · last collected {when(conn.last_success_at)}
          </div>
        </div>
        {!synthetic && (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => run("test")} disabled={busy !== null || (role && !conn.aws_role_arn)}>
              {busy === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
              Test connection
            </Button>
            <Button variant="primary" onClick={() => run("collect")} disabled={busy !== null || active || (role && !conn.aws_role_arn)}>
              {busy === "collect" || active ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CloudDownload className="h-3.5 w-3.5" />}
              {active ? "Collecting…" : "Collect data"}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Delete connection"
              onClick={async () => {
                if (confirm(`Delete the connection "${conn.name}"? Collected usage files stay on disk.`)) {
                  await api.deleteConnection(conn.id);
                  onChange();
                }
              }}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>
      <div className="space-y-4 px-5 pt-3 pb-5">
        {synthetic && <Callout title="Synthetic connection">Demo data generated by the tool; it cannot be tested or collected.</Callout>}
        {role && (
          <div className="space-y-3 rounded-lg border border-border p-4">
            <div className="text-[13px] font-semibold">1. Create the read-only role in the AWS console</div>
            <ol className="list-decimal space-y-1 pl-5 text-[12.5px] text-text-2">
              <li>
                <a href="/api/infra/client-readonly-role.yaml" className="inline-flex items-center gap-1 text-accent hover:underline">
                  <Download className="h-3.5 w-3.5" /> Download the CloudFormation template
                </a>
              </li>
              <li>
                Sign in to the account to analyse (the payer account for an organization) and open <b>CloudFormation → Create stack →
                With new resources</b>.
              </li>
              <li>
                Choose <b>Upload a template file</b>, pick the downloaded file, and name the stack <code>savings-tool-readonly</code>.
              </li>
              <li>
                Parameters: <b>ToolAccountId</b> = the account the tool runs as (for a test on your own machine, your own 12-digit
                account ID) and <b>ExternalId</b> ={" "}
                <code className="rounded bg-surface-2 px-1">{conn.aws_external_id}</code>
                <button
                  type="button"
                  className="ml-1 text-[11px] text-accent hover:underline"
                  onClick={() => navigator.clipboard?.writeText(conn.aws_external_id ?? "")}
                >
                  copy
                </button>
                . Leave the rest as is.
              </li>
              <li>
                Tick <b>I acknowledge that AWS CloudFormation might create IAM resources with custom names</b>, then <b>Submit</b>.
              </li>
              <li>
                When the stack shows CREATE_COMPLETE, open its <b>Outputs</b> tab and copy <b>RoleArn</b>.
              </li>
            </ol>
            <p className="text-[12px] text-muted">
              The tool assumes this role with the credentials on the machine running it, so those still need to be set up (see
              &ldquo;Local AWS profile&rdquo; setup below).
            </p>
            {conn.deploy_command && (
              <details className="text-[12.5px]">
                <summary className="cursor-pointer text-text-2">Alternative: deploy with the AWS CLI</summary>
                <div className="mt-2">
                  <CodeBlock code={conn.deploy_command} />
                </div>
              </details>
            )}
            <div className="text-[13px] font-semibold">2. Paste the RoleArn output</div>
            <div className="flex gap-2">
              <TextInput value={arn} onChange={(e) => setArn(e.target.value)} placeholder="arn:aws:iam::123456789012:role/SavingsToolReadOnly" />
              <Button
                onClick={async () => {
                  setError(null);
                  try {
                    await api.updateConnection(conn.id, { aws_role_arn: arn });
                    onChange();
                  } catch (err) {
                    setError((err as Error).message);
                  }
                }}
                disabled={!arn.trim() || arn === conn.aws_role_arn}
              >
                Save
              </Button>
            </div>
          </div>
        )}
        {conn.last_error && !test && (
          <Callout tone="critical" title="Last error">
            {conn.last_error}
          </Callout>
        )}
        {test && (
          <Callout tone={test.ok ? "good" : "critical"} title={test.ok ? "Connection works" : "Connection failed"}>
            <div className="flex items-start gap-1.5">
              {!test.ok && <XCircle aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-critical" />}
              <span>{test.message}</span>
            </div>
            {test.identity && (
              <div className="mt-1">
                Signed in as <code>{test.identity.arn}</code>
              </div>
            )}
            {test.accounts && test.accounts.length > 0 && (
              <div className="mt-1">
                Accounts: {test.accounts.map((a) => `${a.name ?? a.id} (${a.id})${a.is_payer ? " payer" : ""}`).join(", ")}
              </div>
            )}
          </Callout>
        )}
        {error && <p className="text-[12px] text-critical">{error}</p>}
        {!synthetic && <CostNote />}
        {!synthetic && <RunsTable runs={conn.runs} />}
      </div>
    </Card>
  );
}

export function ConnectionsPanel({ tenantId, onAnalysisReady }: { tenantId: string; onAnalysisReady: () => void }) {
  const [conns, setConns] = useState<Connection[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api
      .connections(tenantId)
      .then((c) => {
        setConns(c);
        setError(null);
      })
      .catch((e) => setError((e as Error).message));
  }, [tenantId]);

  useEffect(load, [load]);

  // Poll while a collection is queued or running; refresh the dashboard once it finishes.
  const active = Boolean(conns?.some((c) => c.runs.some((r) => r.status === "queued" || r.status === "running")));
  const onReady = useRef(onAnalysisReady);
  onReady.current = onAnalysisReady;
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) onReady.current();
    wasActive.current = active;
    if (!active) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [active, load]);

  return (
    <div className="space-y-5">
      <Callout title="How data gets in">
        Test the connection, then collect: usage history (Data Export if configured, otherwise a 13-month Cost Explorer backfill),
        existing commitments, AWS&apos;s own recommendations and public prices for what you run. The analysis re-runs automatically when
        collection finishes. Collection runs in the background worker (<code>arq app.workers.main.WorkerSettings</code>).
      </Callout>
      {error && <Callout tone="critical" title="Can't load connections">{error}</Callout>}
      {conns === null && !error && <Empty>Loading…</Empty>}
      {conns?.map((c) => (
        <ConnectionCard key={c.id} conn={c} onChange={load} onCollected={load} />
      ))}
      {conns && conns.length === 0 && <Empty>No connections yet. Add one below.</Empty>}
      <AddConnection tenantId={tenantId} onCreated={load} />
    </div>
  );
}
