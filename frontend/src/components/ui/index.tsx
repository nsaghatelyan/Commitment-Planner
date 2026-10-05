"use client";

import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border border-border bg-surface shadow-[0_1px_2px_rgba(0,0,0,0.04)]", className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  description,
  action,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
      <div>
        <h2 className="text-[15px] font-semibold text-text">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-text-2">{description}</p>}
      </div>
      {action}
    </div>
  );
}

const badge = cva("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "bg-surface-2 text-text-2",
      accent: "bg-accent-soft text-accent",
      good: "bg-good/12 text-good-text",
      warning: "bg-warning/18 text-text",
      serious: "bg-serious/18 text-text",
      critical: "bg-critical/14 text-critical",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({
  tone,
  className,
  children,
  ...rest
}: VariantProps<typeof badge> & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn(badge({ tone }), className)} {...rest}>
      {children}
    </span>
  );
}

const button = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-lg text-[13px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
  {
    variants: {
      variant: {
        primary: "bg-accent text-white hover:brightness-110",
        secondary: "border border-border bg-surface text-text hover:bg-surface-2",
        ghost: "text-text-2 hover:bg-surface-2 hover:text-text",
      },
      size: { sm: "h-7 px-2.5", md: "h-8 px-3", icon: "h-8 w-8" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export function Button({
  variant,
  size,
  className,
  ...rest
}: VariantProps<typeof button> & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={cn(button({ variant, size }), className)} {...rest} />;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-border bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-md px-2.5 py-1 text-[12px] font-medium capitalize transition-colors",
            o.value === value ? "bg-surface text-text shadow-sm" : "text-text-2 hover:text-text",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  tabs,
  onChange,
}: {
  value: T;
  tabs: { value: T; label: string; count?: number }[];
  onChange: (v: T) => void;
}) {
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-border">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={t.value === value}
          onClick={() => onChange(t.value)}
          className={cn(
            "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-[13px] font-medium whitespace-nowrap transition-colors",
            t.value === value ? "border-accent text-text" : "border-transparent text-text-2 hover:text-text",
          )}
        >
          {t.label}
          {t.count !== undefined && <span className="rounded bg-surface-2 px-1 text-[11px] text-muted tabular">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

const CALLOUT_ICON = { info: Info, warning: AlertTriangle, critical: OctagonAlert, good: CheckCircle2 };
const CALLOUT_TONE = {
  info: "border-accent/30 bg-accent-soft",
  warning: "border-warning/50 bg-warning/10",
  critical: "border-critical/40 bg-critical/8",
  good: "border-good/40 bg-good/8",
};
const CALLOUT_ICON_COLOR = { info: "text-accent", warning: "text-serious", critical: "text-critical", good: "text-good" };

export function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: keyof typeof CALLOUT_TONE;
  title: ReactNode;
  children?: ReactNode;
}) {
  const Icon = CALLOUT_ICON[tone];
  return (
    <div className={cn("flex gap-3 rounded-xl border px-4 py-3", CALLOUT_TONE[tone])}>
      <Icon aria-hidden className={cn("mt-0.5 h-4 w-4 shrink-0", CALLOUT_ICON_COLOR[tone])} />
      <div className="min-w-0 text-[13px]">
        <div className="font-semibold text-text">{title}</div>
        {children && <div className="mt-1 text-text-2">{children}</div>}
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  sub,
  emphasis,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  emphasis?: boolean;
}) {
  return (
    <Card className="px-4 py-3.5">
      <div className="text-[12px] font-medium text-text-2">{label}</div>
      <div className={cn("mt-1 text-[26px] leading-tight font-semibold tracking-tight", emphasis && "text-accent")}>{value}</div>
      {sub && <div className="mt-1 text-[12px] text-muted">{sub}</div>}
    </Card>
  );
}

export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-[1px]" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        className="relative flex h-full w-full max-w-[920px] flex-col border-l border-border bg-bg shadow-2xl"
      >
        <div className="flex items-center justify-between gap-3 border-b border-border bg-surface px-5 py-3">
          <div className="min-w-0 text-[15px] font-semibold">{title}</div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto p-5">{children}</div>
      </aside>
    </div>
  );
}

export function RiskBadge({ risk }: { risk: string | null }) {
  if (!risk) return null;
  const tone = risk === "low" ? "good" : risk === "med" ? "warning" : "critical";
  return (
    <Badge tone={tone} title={`${risk} risk`}>
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {risk === "med" ? "medium" : risk}
    </Badge>
  );
}

export function ActionBadge({ action, urgent }: { action: string; urgent?: boolean }) {
  const label = { purchase: "Buy", renew: "Renew", exchange: "Exchange", flag: "Review" }[action] ?? action;
  const tone = action === "exchange" ? "accent" : action === "renew" ? "warning" : action === "flag" ? "critical" : "neutral";
  return (
    <span className="inline-flex gap-1">
      <Badge tone={tone}>{label}</Badge>
      {urgent && <Badge tone="critical">Urgent</Badge>}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-5 py-10 text-center text-[13px] text-muted">{children}</div>;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-lg bg-surface-2", className)} />;
}
