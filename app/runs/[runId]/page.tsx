import { RunView } from "@/components/run/RunView";

export const dynamic = "force-dynamic";

// Next 16: params is a Promise and must be awaited.
export default async function RunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return <RunView runId={runId} />;
}
