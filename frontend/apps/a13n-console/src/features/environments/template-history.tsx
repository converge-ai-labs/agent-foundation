import { ClockCounterClockwiseIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useState } from "react";
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
import { CatalogStep } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import styles from "./environments.module.css";
import { type EnvironmentScope } from "./api";
import { TemplateConfig } from "./template-config";

/**
 * Every published revision of one template. Restoring opens the old
 * configuration as a draft; publishing it creates a new revision on top.
 */
export function TemplateHistory({
  template,
  scope,
  editable,
  close,
}: {
  template: Schema["EnvironmentTemplate"];
  scope: EnvironmentScope;
  editable: boolean;
  close: () => void;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor(),
    [restore, setRestore] = useState<Schema["EnvironmentTemplateRevision"]>();
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
  if (restore)
    return (
      <CatalogStep
        backLabel={t("Back to versions")}
        onBack={() => setRestore(undefined)}
      >
        <p className={styles.revisionNote}>
          {t(
            "Publishing this draft restores version {{version}} as the current revision.",
            {
              version: restore.version,
            },
          )}
        </p>
        <TemplateConfig
          scope={scope}
          template={template}
          revision={restore}
          close={close}
        />
      </CatalogStep>
    );
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
                {query.data.items.map((item) => {
                  const current = item.id === template.current_revision_id;
                  return (
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
                          {current ? (
                            <StatePill state="active" label={t("Current")} />
                          ) : (
                            editable && (
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setRestore(item)}
                                type="button"
                              >
                                {t("Restore as new revision")}
                              </Button>
                            )
                          )}
                        </span>
                      }
                    />
                  );
                })}
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
