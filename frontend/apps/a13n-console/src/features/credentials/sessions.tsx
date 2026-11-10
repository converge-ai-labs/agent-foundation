import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CopyableId } from "../../shared/identity";
import { Section } from "../../shared/page";
import {
  credentialQueries,
  type SessionCredential,
  type SessionTarget,
} from "./api";
import styles from "./credentials.module.css";

export function applicationLabel(row: SessionCredential) {
  if (row.status === "applied")
    return row.credential_state === "revoked" ? "Removal confirmed" : "Applied";
  return {
    pending: "Awaiting synchronization",
    retry: "Retry pending",
    on_next_use: "On next use",
    on_create: "On creation",
    unknown: "Not confirmed",
  }[row.status];
}

export function CredentialSessions({ target }: { target: SessionTarget }) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const page = useCursor(target);
  const query = useQuery({
    ...credentialQueries(client, workspace.id).sessions(target, page.cursor),
    enabled: can("read"),
    refetchInterval: 10_000,
  });
  return (
    <Section
      title={t("Session application")}
      description={t(
        "Last confirmed application, not a live connection check. Idle Sessions wait until next use.",
      )}
      actions={
        <Button
          type="button"
          size="sm"
          variant="outline"
          loading={query.isFetching}
          onClick={() => void query.refetch()}
        >
          {t("Refresh")}
        </Button>
      }
    >
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : query.data.items.length ? (
        <>
          <ResourceTable
            caption={t("Session application")}
            items={query.data.items.map((row) => ({
              ...row,
              id: JSON.stringify([
                row.thread_id,
                row.environment_id,
                row.binding_name,
                row.session_version,
                row.env,
              ]),
            }))}
            columns={[
              {
                label: t("Environment / Thread"),
                tone: "primary",
                render: (row) => (
                  <div className={styles.summary}>
                    <strong>{row.environment_name}</strong>
                    <CopyableId value={row.thread_id} />
                    <span>
                      {t("Binding {{name}} · version {{version}}", {
                        name: row.binding_name,
                        version: row.session_version,
                      })}
                    </span>
                  </div>
                ),
              },
              {
                label: t("Credential / variable"),
                render: (row) => (
                  <div className={styles.summary}>
                    <span>{row.credential_name}</span>
                    <code>{row.env}</code>
                    <span className={styles.domains}>
                      {row.inject_hosts.join(", ")}
                    </span>
                  </div>
                ),
              },
              {
                label: t("Application status"),
                render: (row) => (
                  <div className={styles.summary}>
                    <StatePill
                      state={
                        row.status === "retry"
                          ? "failed"
                          : row.status === "applied"
                            ? "completed"
                            : row.status
                      }
                      label={t(applicationLabel(row))}
                    />
                    {row.error_code && <code>{row.error_code}</code>}
                    {row.retry_at && (
                      <span>
                        {t("Next retry")}: <Timestamp value={row.retry_at} />
                      </span>
                    )}
                  </div>
                ),
              },
              {
                label: t("Applied / current version"),
                render: (row) => (
                  <div className={styles.summary}>
                    <span>
                      {row.applied_credential_version ?? "—"} /{" "}
                      {row.credential_version}
                    </span>
                    {row.applied_at ? (
                      <Timestamp value={row.applied_at} />
                    ) : (
                      <span>{t("No confirmed application")}</span>
                    )}
                  </div>
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} credential references on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          title={t("No Session references")}
          description={t(
            "References appear when executions select credentials and disappear after confirmed Session retirement.",
          )}
        />
      )}
    </Section>
  );
}
