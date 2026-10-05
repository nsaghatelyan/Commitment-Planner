const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function getApiStatus(): Promise<string> {
  try {
    const res = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    if (!res.ok) return `error (${res.status})`;
    const body = (await res.json()) as { status: string };
    return body.status;
  } catch {
    return "unreachable";
  }
}

export const dynamic = "force-dynamic";

export default async function Home() {
  const status = await getApiStatus();
  return (
    <main style={{ padding: "2rem", fontFamily: "system-ui, sans-serif" }}>
      <h1>Savings Tool</h1>
      <p>AWS and Azure savings plan and reservation recommendations.</p>
      <p>
        API ({apiUrl}): <strong>{status}</strong>
      </p>
    </main>
  );
}
