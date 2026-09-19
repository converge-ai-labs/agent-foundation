import { ClockCounterClockwiseIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  ListRow,
  ListRows,
  ListRowsEmpty,
  Pagination,
  useCursor,
} from "../../shared/collection";
import { Confirm } from "../../shared/dialogs";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import styles from "./environments.module.css";

/**
 * Every published revision of one template. Any of them can become the
 * default that new environments use unless a revision is pinned.
 */
export function TemplateHistory({
  template,
  etag,
  editable,
  reload,
}: {
  template: Schema["EnvironmentTemplate"];
  etag?: string;
  editable: boolean;
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    cache = useQueryClient(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["environment-template-history", template.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-templates/{template_id}/revisions", {
          params: {
            path: { template_id: template.id },
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <div className={styles.versions}>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="list" rows={4} />
      ) : (
        query.data && (
          <>
            {query.data.items.length ? (
              <ListRows>
                {query.data.items.map((item) => (
                  <ListRow
                    key={item.id}
                    icon={
                      <ClockCounterClockwiseIcon
                        aria-hidden="true"
                        className="size-4 text-muted-foreground"
                      />
                    }
                    name={t("Version {{version}}", { version: item.version })}
                    secondary={<Timestamp value={item.created_at} />}
                    actions={
                      <span className={styles.versionMeta}>
                        {item.id === template.default_revision_id ? (
                          <span className="text-xs text-muted-foreground">
                            {t("Default version")}
                          </span>
                        ) : (
                          editable && (
                            <Confirm
                              subject={`${template.name} · v${item.version}`}
                              triggerVariant="ghost"
                              title={t("Set as default")}
                              description={t(
                                "Future runs will use this version unless another version is selected.",
                              )}
                              trigger={t("Set as default")}
                              action={async () => {
                                if (!etag)
                                  throw new Error(
                                    t(
                                      "Version information is unavailable. Reload this page.",
                                    ),
                                  );
                                await client.http
                                  .POST(
                                    "/api/v1/environment-templates/{template_id}/revisions/{revision_id}/default",
                                    {
                                      params: {
                                        path: {
                                          template_id: template.id,
                                          revision_id: item.id,
                                        },
                                        header: { "If-Match": etag },
                                      },
                                    },
                                  )
                                  .then(data);
                                await Promise.all(
                                  [
                                    "environment-templates",
                                    "environment-template-history",
                                    "environment-revision",
                                  ].map((key) =>
                                    cache.invalidateQueries({
                                      queryKey: [key],
                                    }),
                                  ),
                                );
                                await reload();
                              }}
                            />
                          )
                        )}
                      </span>
                    }
                  />
                ))}
              </ListRows>
            ) : (
              <ListRowsEmpty>{t("No revisions yet")}</ListRowsEmpty>
            )}
            <CollectionFooter>
              <Pagination page={page} next={query.data.next_cursor} />
            </CollectionFooter>
          </>
        )
      )}
    </div>
  );
}
