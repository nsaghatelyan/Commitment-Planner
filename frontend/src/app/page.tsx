"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Callout, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .tenants()
      .then((tenants) => {
        if (!tenants.length) {
          setError("No clients yet. Seed the synthetic data: python -m app.synthetic --seed-db --analyze");
          return;
        }
        const pick = tenants.find((t) => t.slug === "synthetic-large") ?? tenants[0];
        router.replace(`/t/${pick.id}`);
      })
      .catch((e) => setError(`Can't reach the API (${e.message}). Is the backend running on :8000?`));
  }, [router]);
  return (
    <main className="mx-auto max-w-xl p-10">
      {error ? <Callout tone="warning" title="Nothing to show">{error}</Callout> : <Skeleton className="h-24" />}
    </main>
  );
}
