import { useEffect, useMemo } from "react";
import { BrowserRouter, Navigate, useLocation } from "react-router";
import { AppRoutes } from "../../../app";
import { AuthProvider } from "../../../auth/context";
import { createFakeService } from "./fake-service";
import { previewScenario } from "./scenario";

export { createFakeService, type FakeService } from "./fake-service";
export { previewScenario } from "./scenario";

/**
 * The real Console over an in-memory Service. Mounted only in development, it
 * renders the same authenticated tree under its own basename so every path and
 * link inside the app stays exactly what production serves.
 */
export function PreviewApp({ basename }: { basename: string }) {
  const speed = Number(
    new URLSearchParams(window.location.search).get("speed") ?? 1,
  );
  const service = useMemo(
    () =>
      createFakeService(previewScenario(), {
        speed: Number.isFinite(speed) && speed > 0 ? speed : 1,
      }),
    [speed],
  );
  useEffect(() => () => service.close(), [service]);
  const entry = service.store.scenario.entry;
  const workspace = service.store.scenario.workspace;
  return (
    <BrowserRouter basename={basename}>
      <AuthProvider fetch={service.fetch}>
        <PreviewEntry
          to={`/workspace/${workspace.key}/sessions/${entry.sessionId}/threads/${entry.threadId}/runs/${entry.runId}`}
        >
          <AppRoutes />
        </PreviewEntry>
      </AuthProvider>
    </BrowserRouter>
  );
}

/** `/preview` itself opens the scenario's current Run. */
function PreviewEntry({
  to,
  children,
}: {
  to: string;
  children: React.ReactNode;
}) {
  const { pathname } = useLocation();
  if (pathname === "/" || pathname === "") return <Navigate to={to} replace />;
  return <>{children}</>;
}
