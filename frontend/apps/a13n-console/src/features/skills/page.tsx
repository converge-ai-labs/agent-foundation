import { Identifier } from "../../shared/copy";
import {
  Badge,
  FormField,
  Input,
  Tabs,
  TabsList,
  TabsPanel,
  TabsTab,
} from "a13n-ui";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  data,
  representation,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  Timestamp,
} from "../../shared/feedback";
import styles from "../../shared/shared.module.css";
import cards from "../../shared/resource-cards.module.css";
import skillStyles from "./skills.module.css";
import { ImportSkill } from "./import";
import { Revisions } from "./revisions";
import { SkillFiles } from "./files";
import { RenameSkill, SkillActions } from "./identity";
import { ResourceReference } from "../../shared/resource-reference";

export function SkillsPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    page = useCursor(),
    navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState<"all" | "zip" | "github">("all");
  const term = search.trim().toLocaleLowerCase();
  const query = useQuery({
    queryKey: ["skills", workspace.id, "list", term, source, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/skills", {
          params: {
            path: { workspace: workspace.id },
            query: {
              cursor: page.cursor,
              ...(term && { q: term }),
              ...(source !== "all" && { source_kind: source }),
            },
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
          <ImportSkill
            onSuccess={(skill) => navigate(encodeURIComponent(skill.key))}
          />
        )
      }
    >
      <div className={styles.filters}>
        <FormField
          label={t("Search skills")}
          hideLabel
          className="min-w-0 w-full"
        >
          <Input
            type="search"
            placeholder={t("Search by name or key…")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              page.reset();
            }}
          />
        </FormField>
        <Tabs
          value={source}
          onValueChange={(value) => {
            setSource(value as typeof source);
            page.reset();
          }}
        >
          <TabsList aria-label={t("Skill source")}>
            <TabsTab value="all">{t("All")}</TabsTab>
            <TabsTab value="github">GitHub</TabsTab>
            <TabsTab value="zip">{t("ZIP upload")}</TabsTab>
          </TabsList>
        </Tabs>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <div className={cards.grid}>
            {query.data.items.map((item) => (
              <article
                key={item.id}
                className={`${cards.card} ${skillStyles.card}`}
              >
                <header>
                  <h2>
                    <Link
                      to={encodeURIComponent(item.key)}
                      className={skillStyles.cardLink}
                    >
                      {item.name}
                    </Link>
                  </h2>
                  <ResourceReference id={item.id} resourceKey={item.key} />
                  <Badge variant="secondary">v{item.version}</Badge>
                </header>
                <footer>
                  <span>
                    {item.source_kind === "github" ? "GitHub" : t("ZIP upload")}
                  </span>
                  <span className={skillStyles.updated}>
                    <span>{t("Updated")}</span>{" "}
                    <Timestamp value={item.updated_at} />
                  </span>
                </footer>
              </article>
            ))}
          </div>
          <Pagination page={page} next={query.data?.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t(
              term || source !== "all" ? "No matching skills" : "No skills yet",
            )}
            description={t(
              term || source !== "all"
                ? "Try another search or source."
                : "Import a skill package to make it available in agent configurations.",
            )}
          />
        )
      )}
    </Page>
  );
}
export function SkillDetail() {
  const { skillKey = "" } = useParams(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [search, setSearch] = useSearchParams();
  const query = useQuery({
    queryKey: ["skills", workspace.id, "key", skillKey],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/skills/{skill_key}", {
          params: { path: { workspace: workspace.id, skill_key: skillKey } },
          headers: workspaceHeaders(workspace.id),
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
  const tab = ["versions", "references"].includes(search.get("tab") ?? "")
    ? search.get("tab")!
    : "files";
  return (
    <Page
      title={skill.name}
      titleAction={
        <>
          <ResourceReference id={skill.id} resourceKey={skill.key} />
          {can("skill.update") && <RenameSkill resource={query.data} />}
        </>
      }
      back={`../skills`}
      actions={
        <>
          {can("skill.revision.publish") && <ImportSkill skill={skill} />}
          {can("skill.delete") && <SkillActions resource={query.data} />}
        </>
      }
    >
      <Tabs
        value={tab}
        onValueChange={(key) => {
          const next = new URLSearchParams(search);
          next.set("tab", String(key));
          next.delete("cursor");
          setSearch(next);
        }}
      >
        <TabsList aria-label={t("Skill details")}>
          <TabsTab value="files">{t("Files")}</TabsTab>
          <TabsTab value="versions">{t("Versions")}</TabsTab>
          <TabsTab value={"references"}>{t("Used by agents")}</TabsTab>
        </TabsList>
        <TabsPanel value="files">
          <SkillFiles
            skill={skill}
            revisionId={search.get("revision") ?? skill.current_revision_id}
          />
        </TabsPanel>
        <TabsPanel value="versions">
          <Revisions skill={skill} />
        </TabsPanel>
        <TabsPanel value={"references"}>
          {<References skill={skill} />}
        </TabsPanel>
      </Tabs>
    </Page>
  );
}
function References({ skill }: { skill: Schema["Skill"] }) {
  const { basePath } = useWorkspace(),
    client = useClient(),
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
          headers: workspaceHeaders(skill.workspace_id),
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
          <ResourceTable
            items={query.data.items.map((item) => ({
              ...item,
              id: item.agent_id,
            }))}
            columns={[
              {
                label: t("Agent"),
                tone: "primary",
                render: (item) => (
                  <Link to={`${basePath}/agents/${item.agent_key}`}>
                    {item.agent_name}
                  </Link>
                ),
              },
              {
                label: t("Revision"),
                tone: "muted",
                render: (item) => <Identifier value={item.agent_revision_id} />,
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
