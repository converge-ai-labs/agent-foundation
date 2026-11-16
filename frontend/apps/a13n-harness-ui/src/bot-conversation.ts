// These Host interruption markers remain durable diagnostics, not actionable
// conversation errors. Unknown/provider failures stay visible in ordinary mode.
const interruptionNotices = new Set([
  "Bot steering was interrupted. Review the reported error and working Thread results before continuing.",
  "Bot decision admission was interrupted. Review the reported error and working Thread results before continuing.",
  "Bot was interrupted. Review the reported error and any effects before continuing.",
  "Bot admission was interrupted. Review the reported error and working Thread results before continuing.",
  "Bot processing was interrupted. Review the reported error and working Thread results before continuing.",
  "Bot did not complete this input. Review the reported error and working Thread results before continuing.",
]);

export function botConversationError(
  error: string | null | undefined,
  debug: boolean,
) {
  return !debug && error && interruptionNotices.has(error) ? undefined : error;
}
