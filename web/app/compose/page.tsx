import { ComposeStudio } from "./ComposeStudio";

export const metadata = { title: "Compose — Exly Outbound" };

export default async function ComposePage({
  searchParams,
}: {
  searchParams: Promise<{ lead?: string; channel?: string }>;
}) {
  const sp = await searchParams;
  const leadId = sp.lead ? Number(sp.lead) : undefined;
  const channel = sp.channel === "email" ? "email" : sp.channel === "whatsapp" ? "whatsapp" : undefined;
  return <ComposeStudio preselectLeadId={Number.isFinite(leadId) ? leadId : undefined} preselectChannel={channel} />;
}
