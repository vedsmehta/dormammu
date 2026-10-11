import { ProposalView } from "@/components/proposals/ProposalView";

export const dynamic = "force-dynamic";

// Next 16: params is a Promise and must be awaited.
export default async function ProposalPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ProposalView proposalId={id} />;
}
