"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { NewClientDialog } from "@/components/dashboard/NewClientDialog";
import { Button, Callout, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [empty, setEmpty] = useState(false);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    api
      .tenants()
      .then((tenants) => {
        if (!tenants.length) {
          setEmpty(true);
          return;
        }
        const pick = tenants.find((t) => t.slug === "synthetic-large") ?? tenants[0];
        router.replace(`/t/${pick.id}`);
      })
      .catch((e) => setError(`Can't reach the API (${e.message}). Is the backend running on :8000?`));
  }, [router]);
  return (
    <main className="mx-auto max-w-xl p-10">
      {error && <Callout tone="warning" title="Nothing to show">{error}</Callout>}
      {empty && (
        <Callout title="No clients yet">
          <p>
            Create a client and connect a cloud account, or load the demo data with{" "}
            <code>python -m app.synthetic --seed-db --analyze</code>.
          </p>
          <Button className="mt-3" variant="primary" onClick={() => setCreating(true)}>
            New client
          </Button>
        </Callout>
      )}
      {!error && !empty && <Skeleton className="h-24" />}
      <NewClientDialog open={creating} onClose={() => setCreating(false)} onCreated={(id) => router.push(`/t/${id}?tab=connections`)} />
    </main>
  );
}
