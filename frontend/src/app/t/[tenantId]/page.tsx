import { Suspense } from "react";

import { Dashboard } from "@/components/dashboard/Dashboard";

export default async function TenantPage({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  return (
    <Suspense>
      <Dashboard tenantId={tenantId} />
    </Suspense>
  );
}
