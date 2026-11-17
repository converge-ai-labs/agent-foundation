import {
  React,
  Button,
  Page,
  Loading,
  ErrorPage,
  useApp,
  useQuery,
  useMutation,
  useQueryClient,
  useWorkspace,
  useAuth,
  useAppearance,
  useTranslation,
  Link,
} from "@a13n/console";
import "./reports.css";
const Details = React.lazy(() => import("./details"));

function Reports() {
  const app = useApp();
  const { workspace } = useWorkspace();
  const { data: identity } = useAuth();
  const { theme } = useAppearance();
  const { i18n } = useTranslation();
  const cache = useQueryClient();
  const [details, setDetails] = React.useState(false);
  const query = useQuery({
    queryKey: app.queryKey("summary"),
    queryFn: ({ signal }) =>
      app.request("/summary", { signal }).then((response) => response.json()),
  });
  const preview = useMutation({
    mutationFn: () =>
      app
        .request("/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "Weekly report" }),
        })
        .then((response) => response.json()),
  });
  const zh = i18n.language.startsWith("zh");
  if (query.isPending) return <Loading page />;
  if (query.error) return <ErrorPage error={query.error} />;
  return (
    <Page
      title={zh ? "报告" : "Reports"}
      description={
        zh
          ? "独立安装的 App，使用当前工作空间。"
          : "An independently installed App for this workspace."
      }
      actions={
        <Button loading={preview.isPending} onClick={() => preview.mutate()}>
          {zh ? "预览报告" : "Preview report"}
        </Button>
      }
    >
      <div className="acme-reports">
        <div className="acme-reports-intro">
          <span className="acme-reports-eyebrow">
            ACME · {zh ? "演示数据" : "DEMO DATA"}
          </span>
          <h2>{zh ? "每周概览" : "The week at a glance"}</h2>
          <p>
            {zh
              ? "查看快照并预览下一份报告。"
              : "Review snapshots and preview your next report."}
          </p>
        </div>
        <dl className="acme-reports-metrics">
          {[
            [zh ? "报告快照" : "Report snapshots", query.data.snapshots],
            [zh ? "数据源" : "Data sources", query.data.sources],
            [zh ? "完成率" : "Completion", `${query.data.completion}%`],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <section className="acme-reports-context">
          <h3>{zh ? "当前上下文" : "Current context"}</h3>
          <p>
            {workspace.name} · {identity.user.value.name}
          </p>
          <dl>
            <div>
              <dt>{zh ? "外观" : "Appearance"}</dt>
              <dd>{theme}</dd>
            </div>
            <div>
              <dt>{zh ? "语言" : "Language"}</dt>
              <dd>{i18n.resolvedLanguage}</dd>
            </div>
            <div>
              <dt>{zh ? "工作空间" : "Workspace"}</dt>
              <dd data-testid="report-workspace">{query.data.workspace_id}</dd>
            </div>
            <div>
              <dt>{zh ? "查询缓存" : "Query cache"}</dt>
              <dd>
                {cache.getQueryData(app.queryKey("summary")) === query.data
                  ? "Shared with Console"
                  : "Loading"}
              </dd>
            </div>
          </dl>
        </section>
        {preview.error && <ErrorPage error={preview.error} />}
        {preview.data && (
          <p role="status">
            {zh ? "预览就绪" : "Preview ready"}: {preview.data.title}
          </p>
        )}
        <div className="acme-reports-actions">
          <Button variant="outline" onClick={() => setDetails(!details)}>
            {zh ? "构建详情" : "Build details"}
          </Button>
          <Link
            to={`/workspace/${workspace.id}/settings/app.acme.reports.reporting`}
          >
            {zh ? "报告设置" : "Report settings"}
          </Link>
        </div>
        {details && (
          <React.Suspense fallback={<Loading />}>
            <Details />
          </React.Suspense>
        )}
      </div>
    </Page>
  );
}
function ReportingSettings() {
  const app = useApp();
  const { i18n } = useTranslation();
  return (
    <p>
      {i18n.language.startsWith("zh")
        ? "报告使用当前组织和工作空间。"
        : "Reports use the current organization and workspace."}{" "}
      <span className="acme-reports-scope">
        {app.organizationId} · {app.workspaceId}
      </span>
    </p>
  );
}
function BrokenPage() {
  throw new Error("Example App rendering failure");
}
export default {
  bridge: 1,
  pages: [
    {
      id: "overview",
      label: { en: "Reports", "zh-CN": "报告" },
      component: Reports,
    },
    {
      id: "failure",
      label: { en: "App recovery demo", "zh-CN": "App 恢复演示" },
      component: BrokenPage,
    },
  ],
  settings: [
    {
      id: "reporting",
      scope: "workspace",
      label: { en: "Report settings", "zh-CN": "报告设置" },
      description: {
        en: "Reporting for this workspace.",
        "zh-CN": "当前工作空间的报告。",
      },
      component: ReportingSettings,
    },
    {
      id: "reporting",
      scope: "organization",
      label: { en: "Report settings", "zh-CN": "报告设置" },
      component: ReportingSettings,
    },
  ],
  workspaceHome: Reports,
};
