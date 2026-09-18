import { ChoiceField } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  InlineLoading,
  Loading,
  Timestamp,
} from "../../shared/feedback";
import { Page } from "../../shared/page";
import { ImportSkill } from "./import-dialog";
import { SkillIcon, SourceChip } from "./source";
import styles from "./skills.module.css";

export function SkillsPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    page = useCursor(),
    navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState("all");
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
              ...(source !== "all" && {
                source_kind: source as "zip" | "github",
              }),
            },
          },
          signal,
        })
        .then(data),
  });
  const importSkill = can("skill.create") ? (
    <ImportSkill
      onSuccess={(skill) => navigate(encodeURIComponent(skill.key))}
    />
  ) : undefined;
  const filtered = !!term || source !== "all";
  return (
    <Page
      title={t("Skills")}
      description={t("Reusable, versioned packages for your agents.")}
      actions={importSkill}
      toolbar={
        <Toolbar
          search={search}
          searchLabel={t("Search skills")}
          onSearchChange={(value) => {
            setSearch(value);
            page.reset();
          }}
          filters={
            <ChoiceField
              label={t("Source")}
              variant="filter"
              value={source}
              onValueChange={(value) => {
                setSource(value);
                page.reset();
              }}
              options={[
                { value: "all", label: t("All sources") },
                { value: "github", label: "GitHub" },
                { value: "zip", label: t("ZIP") },
              ]}
            />
          }
        />
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            className={styles.listTable}
            caption={t("Skills")}
            items={query.data.items}
            onRowActivate={(skill) => navigate(encodeURIComponent(skill.key))}
            columns={[
              {
                label: t("Skill"),
                tone: "primary",
                render: (skill) => (
                  <ResourceIdentity
                    to={encodeURIComponent(skill.key)}
                    name={skill.name}
                    description={skill.key}
                    resourceId={skill.id}
                    resourceKey={skill.key}
                    icon={<SkillIcon />}
                  />
                ),
              },
              {
                label: t("Source"),
                render: (skill) => <SourceChip kind={skill.source_kind} />,
              },
              {
                label: t("Version"),
                render: (skill) => (
                  <span className={styles.version}>v{skill.version}</span>
                ),
              },
              {
                label: t("Used by"),
                tone: "muted",
                render: (skill) => <UsedBy skill={skill} />,
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (skill) => (
                  <Timestamp value={skill.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} skills on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<SkillIcon size={20} />}
            title={t(filtered ? "No matching skills" : "No skills yet")}
            description={t(
              filtered
                ? "Try another search or source."
                : "Import a skill package to make it available in agent configurations.",
            )}
            action={!filtered && importSkill}
          />
        )
      )}
    </Page>
  );
}

/** How many agent configurations currently depend on this skill. */
function UsedBy({ skill }: { skill: { id: string } }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["skills", workspace.id, skill.id, "reference-count"],
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/skills/{skill_id}/references", {
          params: { path: { skill_id: skill.id }, query: { limit: 50 } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
  if (query.isPending) return <InlineLoading width="4rem" />;
  if (!query.data) return <span>—</span>;
  const count = query.data.items.length;
  if (!count) return <span>{t("Unused")}</span>;
  return (
    <span>
      {query.data.next_cursor
        ? t("{{count}}+ agents", { count })
        : t("{{count}} agents", { count })}
    </span>
  );
}
