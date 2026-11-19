// Loaded only by the runtime registry tests; not imported by the application.
import {
  React,
  Button,
  Link,
  useLocation,
  useState,
  useApp,
  useQuery,
  useQueryClient,
  useWorkspace,
  useTranslation,
} from "../src/app-plugins/bridge";
import { controlsPage } from "./app-plugin-controls";

function Overview() {
  const app = useApp();
  const cache = useQueryClient();
  const { workspace } = useWorkspace();
  const { i18n } = useTranslation();
  const query = useQuery({
    queryKey: app.queryKey("report"),
    queryFn: ({ signal }) =>
      app.request("/summary", { signal }).then((response) => response.json()),
  });
  return (
    <div>
      <h1>Installed report</h1>
      <p>
        {workspace.id} {i18n.resolvedLanguage}
      </p>
      <p>{query.data?.title}</p>
      <p>
        {cache.getQueryData(app.queryKey("report")) === query.data
          ? "Host cache"
          : "Loading"}
      </p>
      <Button onClick={() => void app.request("/preview", { method: "POST" })}>
        Preview
      </Button>
    </div>
  );
}
function Stateful() {
  const [draft, setDraft] = useState("");
  const { pathname } = useLocation();
  if (pathname.endsWith("/failure")) throw new Error("Subpage failed");
  return (
    <div>
      <input
        aria-label="Report draft"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <Link to="/apps/acme.reports/stateful/two">Next report</Link>
      <Link to="/apps/acme.reports/stateful/failure">Broken report</Link>
      <p>{pathname}</p>
    </div>
  );
}
function Failure(): never {
  throw new Error("App render failed");
}
export default {
  bridge: 1,
  pages: [
    controlsPage,
    { id: "overview", label: "Reports", component: Overview },
    { id: "failure", label: "Failure", component: Failure },
    { id: "stateful", label: "Stateful", component: Stateful },
  ],
  settings: [
    {
      id: "reporting",
      scope: "workspace",
      label: "Report settings",
      description: "Workspace reports",
      component: Overview,
    },
  ],
  workspaceHome: Overview,
};
