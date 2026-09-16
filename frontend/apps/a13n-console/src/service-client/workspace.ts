import createFetchClient from "openapi-fetch";
import { data } from "./errors.js";
import type { paths } from "./schema.js";
import type { Transport } from "./transport.js";

type ScopedParameters<P> = P extends { path: infer Path }
  ? Omit<P, "path"> &
      (keyof Omit<Path, "workspace"> extends never
        ? { path?: never }
        : { path: Omit<Path, "workspace"> })
  : P;
type ScopedOperation<O> = O extends { parameters: infer P }
  ? Omit<O, "parameters"> & { parameters: ScopedParameters<P> }
  : O;
type WorkspacePaths = {
  [
    P in keyof paths as P extends `/api/v1/workspaces/{workspace}${infer Tail}`
      ? Tail
      : never
  ]: { [M in keyof paths[P]]: ScopedOperation<paths[P][M]> };
};

/** Bind the generated HTTP surface to the API key's authenticated Workspace. */
export async function workspaceHttp(transport: Transport) {
  const context = data(
    await createFetchClient<paths>(transport.httpOptions()).GET(
      "/api/v1/auth/context",
    ),
  );
  if (!context.workspace_id)
    throw new TypeError(
      "Workspace HTTP requires a Workspace-bound credential.",
    );
  return createFetchClient<WorkspacePaths>(
    transport.httpOptions(
      `${transport.baseUrl}/api/v1/workspaces/${encodeURIComponent(context.workspace_id)}`,
    ),
  );
}
