import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { JsonView } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import { type EnvironmentScope } from "./api";
import { TemplateConfig } from "./template-config";

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
      <>
        <Button
          variant="outline"
          onClick={() => setRestore(undefined)}
          type="button"
        >
          <ArrowLeftIcon size={14} aria-hidden="true" />{" "}
          {t("Back to revisions")}
        </Button>
        <TemplateConfig
          scope={scope}
          template={template}
          revision={restore}
          close={close}
        />
      </>
    );
  return (
    <div className={styles.stack}>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading variant="table" columns={4} rows={5} />
      ) : (
        query.data && (
          <>
            <ResourceTable
              items={query.data.items}
              columns={[
                {
                  label: t("Version"),
                  tone: "primary",
                  render: (item) => `v${item.version}`,
                },
                {
                  label: t("Created"),
                  tone: "muted",
                  render: (item) => <Timestamp value={item.created_at} />,
                },
                {
                  label: t("Template configuration"),
                  render: (item) => (
                    <DisclosureSection
                      title={<>{t("View template configuration")}</>}
                    >
                      <JsonView value={item} />
                    </DisclosureSection>
                  ),
                },
                {
                  label: t("Actions"),
                  align: "right",
                  render: (item) =>
                    editable &&
                    item.id !== template.current_revision_id && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setRestore(item)}
                        type="button"
                      >
                        {t("Restore as new revision")}
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
