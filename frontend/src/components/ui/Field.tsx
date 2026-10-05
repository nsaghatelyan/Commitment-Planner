"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block text-[13px]">
      <span className="font-medium text-text">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-[12px] text-muted">{hint}</span>}
    </label>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        "h-8 w-full rounded-lg border border-border bg-surface px-2.5 text-[13px] text-text outline-none placeholder:text-muted focus:border-accent",
        props.className,
      )}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn("h-8 w-full rounded-lg border border-border bg-surface px-2 text-[13px] text-text outline-none focus:border-accent", props.className)}
    />
  );
}

export function Modal({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label={title} className="relative w-full max-w-md rounded-xl border border-border bg-surface p-5 shadow-2xl">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  );
}

export function CodeBlock({ code }: { code: string }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg border border-border bg-surface-2 p-3 text-[12px] leading-relaxed">{code}</pre>
      <button
        className="absolute top-2 right-2 rounded-md border border-border bg-surface px-2 py-0.5 text-[11px] text-text-2 hover:text-text"
        onClick={() => navigator.clipboard?.writeText(code)}
      >
        Copy
      </button>
    </div>
  );
}
