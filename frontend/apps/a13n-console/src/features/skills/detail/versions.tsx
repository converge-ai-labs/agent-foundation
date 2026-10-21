import { MenuItem } from "a13n-ui";
import { CheckCircleIcon, DownloadSimpleIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, type Schema } from "../../../shared/api";
import {
  CollectionFooter,
  Pagination,
  useCursor,
} from "../../../shared/collection";
import { Confirm } from "../../../shared/dialogs";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  Timestamp,
} from "../../../shared/feedback";
import { VersionTable } from "../../agents/versions";
import { useArchiveDownload } from "../archive";
import { revisionsQuery } from "../revisions";
import { SourceChip } from "../source";

/**
 * Published packages, newest first, in the same history an agent keeps. Every
 * version stays downloadable, and any of them can become the default agents
 * receive without a version pin. Opening one shows its files.
 */
export function Revisions({
  skill,
  etag,
}: {
  skill: Schema["Skill"];
  etag?: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    page = useCursor(),
    download = useArchiveDownload();
  const query = useQuery(
    revisionsQuery(client, skill.workspace_id, skill.id, page.cursor),
  );
  if (query.isPending) return <Loading variant="table" columns={5} rows={4} />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return (
    <>
      <VersionTable
        items={query.data.items}
        defaultId={skill.default_revision_id}
        content={{
          label: t("Source"),
          render: (revision) => (
            <SourceChip kind={revision.config.source.kind} />
          ),
        }}
        onRowActivate={(revision) => navigate(`?revision=${revision.id}`)}
        rowMenu={(revision) => (
          <>
            {can("write") &&
              !skill.archived_at &&
              revision.id !== skill.default_revision_id && (
                <Confirm
                  subject={`${skill.name} · v${revision.number}`}
                  title={t("Set as default")}
                  description={t(
                    "Future runs will use this version unless another version is selected.",
                  )}
                  triggerElement={
                    <MenuItem closeOnClick={false}>
                      <CheckCircleIcon aria-hidden="true" />
                      {t("Set as default")}
                    </MenuItem>
                  }
                  action={async () => {
                    if (!etag)
                      throw new Error(
                        t(
                          "Version information is unavailable. Reload this page.",
                        ),
                      );
                    await client
                      .workspace(skill.workspace_id)
                      .POST(
                        "/api/v1/skills/{skill_id}/revisions/{revision_id}/set-default",
                        {
                          params: {
                            path: {
                              skill_id: skill.id,
                              revision_id: revision.id,
                            },
                          },
                          headers: ifMatch(etag),
                        },
                      )
                      .then(data);
                    await cache.invalidateQueries({
                      queryKey: ["skills", workspace.id],
                    });
                  }}
                />
              )}
            <MenuItem onClick={() => download.mutate(revision)}>
              <DownloadSimpleIcon aria-hidden="true" />
              {t("Download ZIP")}
            </MenuItem>
          </>
        )}
      />
      <CollectionFooter
        count={t("{{count}} versions on this page", {
          count: query.data.items.length,
        })}
      >
        <Pagination page={page} next={query.data.next_cursor} />
      </CollectionFooter>
      <ErrorToast error={download.error} />
    </>
  );
}
