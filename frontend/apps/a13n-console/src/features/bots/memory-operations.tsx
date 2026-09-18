import type { MemoryDialogControl } from "./memory-actions";
import { Button, ModalFrame } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { refreshMemory } from "./memory-actions";
import styles from "./bots.module.css";

export function MemoryOperations({
  account,
  scopeId,
  dialog,
}: {
  account: Schema["Account"];
  scopeId: string;
  dialog?: MemoryDialogControl;
}) {
  const [open, setOpen] = useState(false),
    { t } = useTranslation();
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      {...dialog}
      title={t("Memory needs attention")}
      size="lg"
      closeLabel={t("Close")}
      trigger={
        dialog ? undefined : (
          <Button type="button" size="sm" variant="outline">
            {t("Memory needs attention")}
          </Button>
        )
      }
    >
      {(dialog?.open ?? open) && (
        <Operations account={account} scopeId={scopeId} />
      )}
    </ModalFrame>
  );
}
function Operations({
  account,
  scopeId,
}: {
  account: Schema["Account"];
  scopeId: string;
}) {
  const client = useClient(),
    page = useCursor(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["bot-memory-operations", account.id, scopeId, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations",
          {
            params: {
              path: { account_id: account.id, scope_id: scopeId },
              query: { cursor: page.cursor },
            },
            signal,
          },
        )
        .then(data),
  });
  return (
    <div className={styles.publications}>
      <p>
        {t(
          "Inspect uncertain writes and unfinished deletions. Checking a write does not repeat its creation.",
        )}
      </p>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : (
        !query.error &&
        query.data?.items.map((entry) => (
          <Operation
            key={entry.id}
            account={account}
            scopeId={scopeId}
            entry={entry}
          />
        ))
      )}
      {!query.isPending && !query.error && !query.data?.items.length && (
        <p>{t("No pending operations.")}</p>
      )}
      <Pagination page={page} next={query.data?.next_cursor} />
    </div>
  );
}
function Operation({
  account,
  scopeId,
  entry,
}: {
  account: Schema["Account"];
  scopeId: string;
  entry: Schema["DocumentEntry"];
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const path = {
    account_id: account.id,
    scope_id: scopeId,
    document_id: entry.id,
  };
  const action = useMutation({
    retry: false,
    mutationFn: async () => {
      if (entry.state !== "deleting")
        return client.http
          .POST(
            "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations/{document_id}/reconcile",
            { params: { path } },
          )
          .then(data);
      await client.http.DELETE(
        "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents/{document_id}",
        { params: { path } },
      );
      return client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations/{document_id}",
          { params: { path } },
        )
        .then(data);
    },
    onSuccess: () => refreshMemory(cache, account.id),
  });
  return (
    <section className={styles.operationRow}>
      <div>
        <strong>{entry.title}</strong>
        <small>{entry.id}</small>
        <StatePill state={action.data?.state ?? entry.state} />
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={action.isPending}
        onClick={() => action.mutate()}
      >
        {t(entry.state === "deleting" ? "Retry deletion" : "Check result")}
      </Button>
      <ErrorNotice error={action.error} />
      {action.data?.state === "unconfirmed" && (
        <p role="status">
          {t(
            "The provider outcome remains unknown. Do not submit a duplicate document.",
          )}
        </p>
      )}
    </section>
  );
}
