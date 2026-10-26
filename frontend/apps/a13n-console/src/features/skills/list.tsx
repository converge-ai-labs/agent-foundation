import { ChoiceField } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  ArchivedFilter,
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
import { Page, Section } from "../../shared/page";
import { BuiltinSkills } from "./builtin";
import { usedByQuery } from "./detail/used-by";
import { ImportSkill } from "./import-dialog";
import { SkillAvatar, SkillIcon, SourceChip } from "./source";
import styles from "./skills.module.css";

type Source = Schema["SkillRevisionSummary"]["source"]["kind"];

export function SkillsPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState<Source | "builtin" | "all">("all");
  const [archived, setArchived] = useState(false);
  const term = search.trim().toLocaleLowerCase();
  const page = useCursor({ term, source, archived });
  const query = useQuery({
    enabled: source !== "builtin",
    queryKey: [
      "skills",
      workspace.id,
      "list",
      term,
      source,
      archived,
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/skills", {
          params: {
            query: {
              cursor: page.cursor,
              // The chip adds archived items; omitting the filter lists both.
              ...(!archived && { archived: false }),
              ...(term && { q: term }),
              ...((source === "github" || source === "upload") && { source }),
            },
          },
          signal,
        })
        .then(data),
  });
  const importSkill = can("write") ? (
    <ImportSkill onSuccess={(skill) => navigate(skill.id)} />
  ) : undefined;
  const filtered = !!term || source !== "all" || archived;
  return (
    <Page
      title={t("Skills")}
      description={t(
        "Built-in instructions and reusable skill packages for your agents.",
      )}
      actions={importSkill}
      toolbar={
        <Toolbar
          search={search}
          searchLabel={t("Search skills")}
          onSearchChange={setSearch}
          filters={
            <>
              <ChoiceField
                label={t("Source")}
                variant="filter"
                value={source}
                onValueChange={(value) =>
                  setSource(
                    value === "github" ||
                      value === "upload" ||
                      value === "builtin"
                      ? value
                      : "all",
                  )
                }
                options={[
                  { value: "all", label: t("All sources") },
                  { value: "builtin", label: t("Built-in skills") },
                  { value: "github", label: "GitHub" },
                  { value: "upload", label: t("ZIP") },
                ]}
              />
              {source !== "builtin" && (
                <ArchivedFilter value={archived} onChange={setArchived} />
              )}
            </>
          }
        />
      }
    >
      <div className="grid gap-8">
        {(source === "all" || source === "builtin") && (
          <BuiltinSkills search={term} />
        )}
        {source !== "builtin" && (
          <Section title={t("Workspace skills")}>
            <ErrorNotice
              error={query.error}
              retry={() => void query.refetch()}
            />
            {query.isPending ? (
              <Loading variant="table" columns={5} />
            ) : query.data?.items.length ? (
              <>
                <ResourceTable
                  className={styles.listTable}
                  caption={t("Skills")}
                  items={query.data.items}
                  onRowActivate={(skill) => navigate(skill.id)}
                  columns={[
                    {
                      label: t("Skill"),
                      tone: "primary",
                      render: (skill) => (
                        <ResourceIdentity
                          to={skill.id}
                          name={skill.name}
                          description={skill.description || skill.id}
                          resourceId={skill.id}
                          icon={
                            <SkillAvatar
                              name={skill.name}
                              id={skill.id}
                              className="size-8 rounded-lg"
                            />
                          }
                        />
                      ),
                    },
                    {
                      label: t("Source"),
                      render: (skill) =>
                        skill.default_revision ? (
                          <SourceChip
                            kind={skill.default_revision.source.kind}
                          />
                        ) : (
                          <span>—</span>
                        ),
                    },
                    {
                      label: t("Version"),
                      render: (skill) =>
                        skill.default_revision ? (
                          <span className={styles.version}>
                            v{skill.default_revision.number}
                          </span>
                        ) : (
                          <span>—</span>
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
                  title={t(
                    filtered ? "No matching skills" : "No workspace skills yet",
                  )}
                  description={t(
                    filtered
                      ? "Try another search or source."
                      : "Import a skill package to make it available in agent configurations.",
                  )}
                  action={!filtered && importSkill}
                />
              )
            )}
          </Section>
        )}
      </div>
    </Page>
  );
}

/** How many unarchived agents have a revision that depends on this skill. */
function UsedBy({ skill }: { skill: Schema["Skill"] }) {
  const client = useClient(),
    { t } = useTranslation();
  const query = useQuery({
    ...usedByQuery(client, skill),
    staleTime: 60_000,
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
