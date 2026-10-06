const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const usdCompact = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

export function money(v: number | null | undefined, opts: { cents?: boolean; compact?: boolean } = {}) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (opts.compact && Math.abs(v) >= 10_000) return usdCompact.format(v);
  return (opts.cents || Math.abs(v) < 100 ? usd2 : usd0).format(v);
}

export function pct(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

export function num(v: number | null | undefined, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v.toFixed(digits)).toLocaleString("en-US");
}

export function hourly(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  return `$${v.toFixed(3)}/h`;
}

export function term(months: number) {
  return months % 12 === 0 ? `${months / 12}y` : `${months}m`;
}

const PAYMENT: Record<string, string> = {
  no_upfront: "No upfront",
  partial_upfront: "Partial upfront",
  all_upfront: "All upfront",
  monthly: "Monthly",
};

export function payment(p: string | null | undefined) {
  if (!p) return "Monthly";
  return PAYMENT[p] ?? p;
}

const KIND: Record<string, string> = {
  aws_sp_compute: "Compute SP",
  aws_sp_ec2: "EC2 Instance SP",
  aws_sp_sagemaker: "SageMaker SP",
  aws_sp_database: "Database SP",
  aws_ri: "Reserved Instance",
  azure_sp_compute: "Azure Savings Plan",
  azure_ri: "Azure Reservation",
};

export function kindLabel(kind: string) {
  return KIND[kind] ?? kind;
}

export function isSavingsPlan(kind: string) {
  return kind.includes("_sp_");
}

export function shortDate(iso: string) {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function serviceLabel(service: string | null) {
  const map: Record<string, string> = {
    ec2: "EC2",
    rds: "RDS",
    elasticache: "ElastiCache",
    opensearch: "OpenSearch",
    redshift: "Redshift",
    memorydb: "MemoryDB",
    dynamodb: "DynamoDB",
    docdb: "DocumentDB",
    neptune: "Neptune",
    timestream: "Timestream",
    dms: "DMS",
    keyspaces: "Keyspaces",
    dsql: "Aurora DSQL",
    fargate: "Fargate",
    lambda: "Lambda",
    sagemaker: "SageMaker",
    compute: "Compute",
    database: "Databases",
  };
  return service ? map[service] ?? service : "—";
}

/** "aws_ri / ec2 / us-east-1 / m5 / Linux" -> "Reserved Instance · EC2 · us-east-1 · m5 · Linux" */
export function poolLabel(label: string) {
  const [kind, ...rest] = label.split(" / ");
  return [kindLabel(kind), ...rest.map((p) => serviceLabel(p))].join(" · ");
}
