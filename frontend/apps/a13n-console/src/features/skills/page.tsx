import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, representation, type Schema } from "../../shared/api";
import {
  Page,
  Loading,
  ErrorNotice,
  Empty,
  Timestamp,
} from "../../shared/feedback";
import { Table, Pagination, useCursor } from "../../shared/collection";
import { Confirm, FormActions, JsonView } from "../../shared/form";
import { downloadBlob } from "../../shared/download";
import { ImportSkill } from "./import";
import styles from "../../shared/shared.module.css";

export function SkillsPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    page = useCursor(),
    navigate = useNavigate();
  const query = useQuery({
    queryKey: ["skills", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/skills", {
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <Page
      title={t("Skills")}
      description={t("Reusable, versioned packages for your agents.")}
      actions={
        can("skill.create") && (
          <ImportSkill onSuccess={(skill) => navigate(skill.id)} />
        )
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
                render: (item) => (
                  <Link to={item.id}>
                    <strong>{item.name}</strong>
                    <small>{item.key}</small>
                  </Link>
                ),
              },
              { label: t("Version"), render: (item) => `v${item.version}` },
              {
                label: t("Updated"),
                render: (item) => <Timestamp value={item.updated_at} />,
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No skills yet")}
            description={t(
              "Import a skill package to make it available in agent configurations.",
            )}
          />
        )
      )}
    </Page>
  );
}
export function SkillDetail() {
  const { skillId = "" } = useParams(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [tab, setTab] = useState("revisions"),
    [generation, setGeneration] = useState(0);
  const query = useQuery({
    queryKey: ["skills", workspace.id, skillId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/skills/{skill_id}", {
          params: { path: { skill_id: skillId } },
          signal,
        })
        .then(representation),
  });
  if (query.isPending) return <Loading />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const skill = query.data.value;
  return (
    <Page
      title={skill.name}
      description={skill.key}
      back={`../skills`}
      actions={can("skill.revision.publish") && <ImportSkill skill={skill} />}
    >
      <Tabs
        label={t("Skill details")}
        value={tab}
        onValueChange={setTab}
        items={[
          {
            value: "revisions",
            label: t("Revisions"),
            content: <Revisions skill={skill} />,
          },
          {
            value: "references",
            label: t("Used by agents"),
            content: <References skill={skill} />,
          },
          {
            value: "settings",
            label: t("Settings"),
            content: (
              <SkillSettings
                key={generation}
                initial={query.data}
                reload={() => {
                  void query.refetch().then((result) => {
                    if (!result.error) setGeneration((value) => value + 1);
                  });
                }}
              />
            ),
          },
        ]}
      />
    </Page>
  );
}
function Revisions({ skill }: { skill: Schema["Skill"] }) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "skills",
      skill.workspace_id,
      skill.id,
      "revisions",
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/skills/{skill_id}/revisions", {
          params: {
            path: { skill_id: skill.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  const download = useMutation({
    mutationFn: async (revision: Schema["SkillRevision"]) =>
      downloadBlob(
        data(
          await client.http.GET(
            "/api/v1/skill-revisions/{skill_revision_id}/content",
            {
              params: { path: { skill_revision_id: revision.id } },
              parseAs: "blob",
            },
          ),
        ),
        `${skill.key}-v${revision.version}.zip`,
      ),
  });
  return (
    <div className={styles.stack}>
      <ErrorNotice error={query.error ?? download.error} />
      {query.isPending ? (
        <Loading />
      ) : (
        query.data && (
          <>
            <Table
              items={query.data.items}
              columns={[
                {
                  label: t("Version"),
                  render: (item) => (
                    <strong>
                      v{item.version}
                      {item.id === skill.current_revision_id && (
                        <small>{t("Current")}</small>
                      )}
                    </strong>
                  ),
                },
                {
                  label: t("Source"),
                  render: (item) => (
                    <details>
                      <summary>{item.imported_from.kind}</summary>
                      <JsonView value={item.imported_from} />
                    </details>
                  ),
                },
                {
                  label: t("Package"),
                  render: (item) => (
                    <details>
                      <summary>{t("Manifest")}</summary>
                      <JsonView value={item.manifest} />
                    </details>
                  ),
                },
                {
                  label: t("Created"),
                  render: (item) => <Timestamp value={item.created_at} />,
                },
                {
                  label: t("Actions"),
                  render: (item) => (
                    <Button
                      size="sm"
                      onClick={() => download.mutate(item)}
                      loading={
                        download.isPending && download.variables?.id === item.id
                      }
                    >
                      {t("Download ZIP")}
                    </Button>
                  ),
                },
              ]}
            />
            <Pagination page={page} next={query.data.next_cursor} />
          </>
        )
      )}
    </div>
  );
}
function References({ skill }: { skill: Schema["Skill"] }) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "skills",
      skill.workspace_id,
      skill.id,
      "references",
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/skills/{skill_id}/references", {
          params: {
            path: { skill_id: skill.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        {t(
          "Current revisions of unarchived agents referencing this skill prevent deletion.",
        )}
      </p>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items.map((item) => ({
              ...item,
              id: item.agent_id,
            }))}
            columns={[
              {
                label: t("Agent"),
                render: (item) => (
                  <Link
                    to={`/workspaces/${skill.workspace_id}/agents/${item.agent_id}`}
                  >
                    {item.agent_name}
                  </Link>
                ),
              },
              {
                label: t("Revision"),
                render: (item) => <code>{item.agent_revision_id}</code>,
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No active references")}
            description={t("No current agent configuration uses this skill.")}
          />
        )
      )}
    </div>
  );
}
function SkillSettings({
  initial,
  reload,
}: {
  initial: ReturnType<typeof representation<Schema["Skill"]>>;
  reload: () => void;
}) {
  const [basis] = useState(initial),
    [name, setName] = useState(initial.value.name),
    client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const params = {
    path: { skill_id: basis.value.id },
    header: { "If-Match": basis.etag ?? "" },
  };
  const save = useMutation({
    mutationFn: () =>
      client.http
        .PATCH("/api/v1/skills/{skill_id}", { params, body: { name } })
        .then(data),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["skills"] });
    },
  });
  return (
    <div className={styles.stack}>
      {can("skill.update") && (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate();
          }}
        >
          <Input
            label={t("Display name")}
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
          <ErrorNotice error={save.error} retry={reload} />
          <FormActions pending={save.isPending} />
        </form>
      )}
      {can("skill.delete") && (
        <Confirm
          title={t("Delete skill")}
          description={t(
            "This removes the skill and its revisions from ordinary access. Agents currently using this skill must be updated first.",
          )}
          trigger={t("Delete skill")}
          danger
          action={async () => {
            await client.http.DELETE("/api/v1/skills/{skill_id}", { params });
            navigate(`/workspaces/${basis.value.workspace_id}/skills`);
          }}
        />
      )}
    </div>
  );
}
