import { ProposalView } from "@/components/research/ProposalView";

export const dynamic = "force-dynamic";

// Next 16: params is a Promise and must be awaited.
export default async function ProposalPage({ params }: { params: Promise<{ proposalId: string }> }) {
  const { proposalId } = await params;
  return <ProposalView proposalId={proposalId} />;
}
