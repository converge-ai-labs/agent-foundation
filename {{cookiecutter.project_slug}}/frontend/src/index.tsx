import {
  Badge, Button, DetailHeader, DetailPage, Empty, FormActions, FormField, FormSection,
  Input, Link, Page, React, ResourceTable, Textarea, Toolbar, bridgeVersion, useSearchParams,
  type AppContribution,
{% if cookiecutter.surface == 'full-stack' -%}
  ErrorPage, Loading, useApp, useQuery,
{% endif -%}
} from "@a13n/console";
import styles from "./app.module.css";

interface Report {
  id: string;
  name: string;
  description: string;
  status: "Ready" | "Draft";
}

const sampleReports: Report[] = [
  { id: "activity", name: "Workspace activity", description: "Review recent activity across your workspace.", status: "Ready" },
  { id: "handoff", name: "Weekly handoff", description: "Summarize work in progress for the next owner.", status: "Draft" },
  { id: "readiness", name: "Release readiness", description: "Track what needs to happen before release.", status: "Draft" },
];

function ReportStatus({ status }: { status: Report["status"] }) {
  return <Badge variant={status === "Ready" ? "success" : "secondary"}>{status}</Badge>;
}

function ReportDetail({ report, onSave, onCancel }: {
  report: Report;
  onSave: (report: Report) => void;
  onCancel: () => void;
}) {
  const [name, setName] = React.useState(report.name);
  const [description, setDescription] = React.useState(report.description);
  const changed = name.trim() !== report.name || description.trim() !== report.description;

  return (
    <DetailPage
      back="?"
      backLabel="Reports"
      header={<DetailHeader name={report.name} status={<ReportStatus status={report.status} />} />}
    >
      <form className={styles.form} onSubmit={(event) => {
        event.preventDefault();
        if (name.trim() && changed) onSave({ ...report, name: name.trim(), description: description.trim() });
      }}>
        <FormSection title="Report details" description="Changes apply on this page and reset when you leave the App or reload.">
          <FormField label="Name">
            <Input required maxLength={80} value={name} onChange={(event) => setName(event.target.value)} />
          </FormField>
          <FormField label="Description">
            <Textarea rows={3} maxLength={240} value={description} onChange={(event) => setDescription(event.target.value)} />
          </FormField>
        </FormSection>
        <FormActions pending={false} disabled={!name.trim() || !changed} onCancel={onCancel} />
      </form>
    </DetailPage>
  );
}

function Reports({ titleAction }: { titleAction?: React.ReactNode }) {
  const [reports, setReports] = React.useState(sampleReports);
  const [search, setSearch] = React.useState("");
  const [params, setParams] = useSearchParams();
  const selected = reports.find((report) => report.id === params.get("report"));
  const close = () => setParams({}, { replace: true });

  if (selected) {
    return <ReportDetail key={selected.id} report={selected} onCancel={close} onSave={(updated) => {
      setReports((current) => current.map((report) => report.id === updated.id ? updated : report));
      setSearch("");
      close();
    }} />;
  }

  const filter = search.trim().toLowerCase();
  const filtered = reports.filter(({ name, description }) => `${name} ${description}`.toLowerCase().includes(filter));
  return (
    <Page
      title={ {{ cookiecutter.project_name | tojson }} }
      description="Browse and edit sample reports. Changes are not persisted."
      titleAction={titleAction}
      toolbar={<Toolbar search={search} onSearchChange={setSearch} searchLabel="Search reports" />}
    >
      {filtered.length ? (
        <ResourceTable
          caption="Sample reports"
          items={filtered}
          columns={[
            {
              label: "Report",
              tone: "primary",
              render: (report) => <Link className={styles.reportLink} to={`?report=${report.id}`}>{report.name}</Link>,
            },
            { label: "Description", render: (report) => report.description },
            { label: "Status", render: (report) => <ReportStatus status={report.status} /> },
          ]}
        />
      ) : (
        <Empty
          title="No matching reports"
          description="Try a different name or description."
          action={<Button variant="outline" onClick={() => setSearch("")}>Clear search</Button>}
        />
      )}
    </Page>
  );
}

{% if cookiecutter.surface == 'full-stack' -%}
interface Summary {
  message: string;
  workspace_id: string;
}

function Overview() {
  const app = useApp();
  const query = useQuery({
    queryKey: app.queryKey("summary"),
    queryFn: async ({ signal }): Promise<Summary> => {
      const response = await app.request("/summary", { signal });
      if (!response.ok) throw new Error(`Summary request failed (${response.status})`);
      return response.json();
    },
  });
  if (query.isPending) return <Loading page />;
  if (query.isError) return <ErrorPage error={query.error} />;
  return <Reports titleAction={<Badge variant="success" title={query.data.message}>Backend connected</Badge>} />;
}
{% endif %}
export default {
  bridge: bridgeVersion,
  pages: [{ id: "overview", label: { en: {{ cookiecutter.project_name | tojson }} }, component: {% if cookiecutter.surface == 'full-stack' %}Overview{% else %}Reports{% endif %} }],
} satisfies AppContribution;
