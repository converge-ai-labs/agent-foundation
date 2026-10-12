import createFetchClient from "openapi-fetch";
import type { paths } from "./schema.js";
import {
  Transport,
  workspaceHeaders,
  type ClientOptions,
} from "./transport.js";
import {
  sessionStream,
  type SessionStreamOptions,
} from "./streams/session-stream.js";

/** One typed HTTP boundary; resource operations stay defined by Service OpenAPI. */
export function createClient(options: ClientOptions) {
  const transport = new Transport(options);
  return {
    /** Routes outside a workspace: auth, users, organizations and workspace administration. */
    http: createFetchClient<paths>(transport.httpOptions()),
    /** Business routes, acting in the named workspace. */
    workspace: (workspaceId: string) =>
      createFetchClient<paths>({
        ...transport.httpOptions(),
        headers: workspaceHeaders(workspaceId),
      }),
    /** A business link the Service returned, such as an agent's `image_url`. */
    workspaceBlob: async (
      workspaceId: string,
      link: string,
      signal?: AbortSignal,
    ) => {
      const response = await transport.fetch(
        new Request(`${transport.baseUrl}${link}`, {
          headers: workspaceHeaders(workspaceId),
          signal,
        }),
      );
      return response.blob();
    },
    setCsrfToken: (token: string | undefined) => transport.setCsrfToken(token),
    streamSession: (
      workspaceId: string,
      sessionId: string,
      streamOptions?: SessionStreamOptions,
    ) => sessionStream(transport, workspaceId, sessionId, streamOptions),
    close: () => transport.close(),
  };
}
export type Client = ReturnType<typeof createClient>;
