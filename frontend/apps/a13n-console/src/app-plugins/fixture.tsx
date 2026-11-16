// Loaded only by the runtime registry tests; not imported by the application.
import {
  React,
  Button,
  useApp,
  useQuery,
  useQueryClient,
  useWorkspace,
  useTranslation,
} from "./bridge";
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
function Failure(): never {
  throw new Error("App render failed");
}
export default {
  bridge: 1,
  pages: [
    { id: "overview", label: "Reports", component: Overview },
    { id: "failure", label: "Failure", component: Failure },
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
