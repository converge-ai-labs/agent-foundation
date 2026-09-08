import {
  notifications,
  type NotificationOptions,
} from "./streams/notifications.js";
import createFetchClient from "openapi-fetch";
import type { paths } from "./schema.js";
import { Transport, type ClientOptions } from "./transport.js";
import { runStream, type RunStreamOptions } from "./streams/run-stream.js";

/** One typed HTTP boundary; resource operations stay defined by Service OpenAPI. */
export function createClient(options: ClientOptions) {
  const transport = new Transport(options);
  return {
    http: createFetchClient<paths>({
      baseUrl: transport.baseUrl,
      fetch: transport.fetch,
      bodySerializer: (body: unknown) =>
        body instanceof Blob || body instanceof ReadableStream
          ? body
          : JSON.stringify(body),
    }),
    setCsrfToken: (token: string | undefined) => transport.setCsrfToken(token),
    streamRun: (runId: string, streamOptions?: RunStreamOptions) =>
      runStream(transport, runId, streamOptions),
    notifications: (notificationOptions: NotificationOptions) =>
      notifications(options, transport.signal, notificationOptions),
    close: () => transport.close(),
  };
}
export type Client = ReturnType<typeof createClient>;
