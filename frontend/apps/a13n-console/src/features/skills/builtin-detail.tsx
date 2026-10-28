import { Button } from "a13n-ui";
import { DownloadSimpleIcon } from "@phosphor-icons/react";
import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { downloadBlob } from "../../shared/download";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { DetailHeader, DetailPage } from "../../shared/page";
import { SkillFiles } from "./detail/files";
import { SkillAvatar } from "./source";
import styles from "./skills.module.css";

function builtinArchiveQuery(
  client: ReturnType<typeof useClient>,
  workspaceId: string,
  skill: Schema["BuiltinSkillDetail"],
) {
  return queryOptions({
    queryKey: [
      "builtin-skills",
      workspaceId,
      skill.name,
      "archive",
      skill.package_digest,
    ],
    queryFn: async ({ signal }) =>
      new Uint8Array(
        data(
          await client
            .workspace(workspaceId)
            .GET("/api/v1/builtin-skills/{name}/content", {
              params: {
                path: { name: skill.name },
                query: { package_digest: skill.package_digest },
              },
              parseAs: "arrayBuffer",
              signal,
            }),
        ),
      ),
    staleTime: Infinity,
    gcTime: 60_000,
  });
}

export function BuiltinSkillDetail() {
  const { name = "" } = useParams(),
    client = useClient(),
    { workspace, basePath } = useWorkspace();
  const query = useQuery({
    queryKey: ["builtin-skills", workspace.id, name],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/builtin-skills/{name}", {
          params: { path: { name } },
          signal,
        })
        .then(data),
  });
  if (query.isPending) return <Loading variant="detail" page />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return (
    <BuiltinPackage
      key={`${workspace.id}:${query.data.package_digest}`}
      skill={query.data}
      back={`${basePath}/skills`}
    />
  );
}

function BuiltinPackage({
  skill,
  back,
}: {
  skill: Schema["BuiltinSkillDetail"];
  back: string;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const archive = builtinArchiveQuery(client, workspace.id, skill);
  const reload = () =>
    void cache.invalidateQueries({
      queryKey: ["builtin-skills", workspace.id, skill.name],
    });
  const download = useMutation({
    mutationFn: async () => {
      const bytes = await cache.fetchQuery(archive);
      downloadBlob(
        new Blob([bytes], { type: "application/zip" }),
        `${skill.name}.zip`,
      );
    },
  });
  return (
    <DetailPage
      back={back}
      backLabel={t("Skills")}
      header={
        <DetailHeader
          avatar={
            <SkillAvatar
              name={skill.name}
              id={skill.name}
              className={styles.detailAvatar}
            />
          }
          name={skill.name}
          description={t(skill.description)}
          status={
            <span className={styles.sourceChip}>{t("Built-in skill")}</span>
          }
          actions={
            <Button
              variant="outline"
              disabled={download.isPending}
              onClick={() => download.mutate()}
            >
              <DownloadSimpleIcon size={16} />
              {t("Download ZIP")}
            </Button>
          }
        />
      }
    >
      <p className={styles.retained}>
        {t("Read-only. Updated with Service releases.")}
      </p>
      <ErrorNotice error={download.error} />
      <SkillFiles manifest={skill} archive={archive} onRetry={reload} />
    </DetailPage>
  );
}
