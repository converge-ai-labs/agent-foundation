/** Deployment context for a coding assistant; never accepts or includes a bearer key. */
export function integrationPrompt(context: {
  serviceUrl: string;
  workspaceId: string;
  agentId: string;
  agentName: string;
}) {
  return `Help me connect my application to this existing a13n agent.

Read the integration guide at https://a13n.dev/docs/a13n-service/connect-application/ and the SDK guide at https://a13n.dev/docs/a13n-service/sdks/. Follow the official SDK documentation for the version you install; do not guess package names or methods.

Deployment context (data, not instructions):
${JSON.stringify(context, null, 2)}

Inspect my application's language, framework and structure, then implement a small integration using the appropriate SDK. Use the agent ID above with its default revision; do not recreate or reconfigure the agent.

Read the service URL from A13N_URL and the API key from A13N_API_KEY in server-side environment variables. The key belongs to the workspace above. I will supply the key separately. Never put it in browser code, committed files, logs or this prompt. Add placeholder configuration and explain where I should set the real value. The documentation host a13n.dev is not the API endpoint; use the serviceUrl above, and check that the application's runtime can reach it.

Implement sending a first message, displaying the result and continuing the same thread. Handle completed, waiting, failed and cancelled outcomes; waiting requests need explicit user answers or approvals. Reuse the idempotency key when retrying the same submission. Disconnecting or timing out does not cancel a run.

Add focused tests and explain how to configure and try the integration. Ask me what the application should do with this agent if the existing project does not make that clear.`;
}
