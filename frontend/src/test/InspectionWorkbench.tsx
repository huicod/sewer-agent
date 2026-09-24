import InspectionConversation from "../InspectionConversation";

// Existing domain regressions exercise a conversation with its evidence panel open.
// The independent shell and its default collapsed layout have separate coverage.
export default function InspectionWorkbench(props: { pollIntervalMs?: number }) {
  return <InspectionConversation {...props} initialDetailsOpen />;
}
