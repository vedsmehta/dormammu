import { CallView } from "@/components/calls/CallView";

export const dynamic = "force-dynamic";

// Next 16: params and searchParams are Promises and must be awaited. There is no per-call endpoint,
// so the call's run id rides along as ?run=<runId>; without it the view scans recent runs.
export default async function CallPage({
  params,
  searchParams,
}: {
  params: Promise<{ callId: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { callId } = await params;
  const sp = await searchParams;
  const raw = sp.run;
  const runId = typeof raw === "string" && raw.length > 0 ? raw : Array.isArray(raw) && raw[0] ? raw[0] : null;
  return <CallView callId={callId} runId={runId} />;
}
