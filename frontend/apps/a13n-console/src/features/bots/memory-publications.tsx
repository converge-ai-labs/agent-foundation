import type { BotAccount } from "./account";
import { Button, ModalFrame } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Pagination, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm } from "../../shared/form";
import { MarkdownContent } from "../../shared/markdown";
import { refreshMemory } from "./memory-actions";
import { GroupPicker, useMemoryGroups } from "./memory-groups";
import styles from "./bots.module.css";

type Props = { account: BotAccount; scopeId: string; sourceId?: string };
export function MemoryPublications(props: Props) {
  const [open, setOpen] = useState(false),
    { t } = useTranslation();
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Published copies")}
      size="lg"
      closeLabel={t("Close")}
      trigger={
        <Button type="button" variant="outline" size="sm">
          {t("Published copies")}
        </Button>
      }
    >
      {open && <PublicationBrowser {...props} />}
    </ModalFrame>
  );
}

function PublicationBrowser({ account, scopeId, sourceId }: Props) {
  const { t } = useTranslation(),
    client = useClient(),
    page = useCursor();
  const [selected, setSelected] = useState<Schema["DocumentEntry"]>();
  const query = useQuery({
    queryKey: [
      "bot-memory-publications",
      account.id,
      scopeId,
      sourceId,
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/publications",
          {
            params: {
              path: { account_id: account.id, scope_id: scopeId },
              query: { source_id: sourceId, cursor: page.cursor },
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
          "Published copies are immutable. You can change who receives a copy or withdraw it.",
        )}
      </p>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending || query.isFetching ? (
        <Loading />
      ) : (
        !query.error && (
          <div className={styles.publicationList}>
            {query.data?.items.map((item) => (
              <button
                key={item.id}
                type="button"
                className={styles.documentItem}
                aria-pressed={selected?.id === item.id}
                onClick={() => setSelected(item)}
              >
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.activity_date}</small>
                </span>
                <StateBadge state={item.state} />
              </button>
            ))}
            {!query.data?.items.length && <p>{t("No published copies.")}</p>}
          </div>
        )
      )}
      <Pagination page={page} next={query.data?.next_cursor} />
      {selected?.state === "active" ? (
        <PublicationDetail
          key={`${selected.id}:${selected.version}`}
          account={account}
          scopeId={scopeId}
          entry={selected}
          onWithdraw={() => setSelected(undefined)}
        />
      ) : (
        selected && (
          <p>
            {t(
              "This copy is not active. Use Pending operations to inspect an unfinished change.",
            )}
          </p>
        )
      )}
    </div>
  );
}

function PublicationDetail({
  account,
  scopeId,
  entry,
  onWithdraw,
}: {
  account: BotAccount;
  scopeId: string;
  entry: Schema["DocumentEntry"];
  onWithdraw: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [draft, setDraft] = useState<string[]>(),
    [review, setReview] = useState(false),
    [basis, setBasis] = useState<number>();
  const path = {
    account_id: account.id,
    scope_id: scopeId,
    document_id: entry.id,
  };
  const query = useQuery({
    queryKey: ["bot-memory-publication", account.id, scopeId, entry.id],
    queryFn: async ({ signal }) => {
      const [document, audience] = await Promise.all([
        client.http
          .GET(
            "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/publications/{document_id}",
            { params: { path }, signal },
          )
          .then(data),
        client.http
          .GET(
            "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/publications/{document_id}/audience",
            { params: { path }, signal },
          )
          .then(data),
      ]);
      return { document, audience };
    },
  });
  const groups = useMemoryGroups(account);
  const save = useMutation({
    mutationFn: async () => {
      if (!query.data || !draft) return;
      await client.http.PATCH(
        "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/publications/{document_id}/audience",
        {
          params: { path },
          body: {
            expected_version: basis ?? query.data.audience.version,
            recipient_scope_ids: draft,
          },
        },
      );
    },
    onSuccess: async () => {
      setDraft(undefined);
      setBasis(undefined);
      setReview(false);
      await refreshMemory(cache, account.id);
    },
  });
  if (query.isPending || query.isFetching) return <Loading />;
  if (query.error || !query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const { document, audience } = query.data;
  const selected = draft ?? audience.recipient_scope_ids;
  return (
    <section
      className={styles.publicationDetail}
      aria-label={t("Published copy details")}
    >
      <h3>{document.title}</h3>
      <MarkdownContent text={document.text} />
      <GroupPicker
        groups={groups}
        exclude={scopeId}
        value={selected}
        onChange={(ids) => {
          setDraft(ids);
          setBasis(basis ?? audience.version);
          setReview(false);
        }}
        disabled={save.isPending}
      />
      {review && (
        <p role="status">
          {t("Confirm the complete recipient list")}:{" "}
          {selected
            .map(
              (id) => groups.data?.find((group) => group.id === id)?.name ?? id,
            )
            .join(", ") || t("No recipients")}
        </p>
      )}
      <ErrorNotice error={save.error} retry={() => void query.refetch()} />
      <div className={styles.memoryActions}>
        <Button
          type="button"
          variant="outline"
          disabled={!draft || save.isPending}
          onClick={() => (review ? save.mutate() : setReview(true))}
        >
          {t(review ? "Confirm recipients" : "Review recipients")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          disabled={!draft || save.isPending}
          onClick={() => {
            setDraft(undefined);
            setBasis(undefined);
            setReview(false);
            save.reset();
          }}
        >
          {t("Discard recipient changes")}
        </Button>
        <Confirm
          title={t("Withdraw copy")}
          subject={document.title}
          trigger={t("Withdraw copy")}
          danger
          description={t(
            "This copy will no longer be available to any recipient group. The source memory stays unchanged. Previously delivered content cannot be retracted.",
          )}
          action={async () => {
            await client.http.POST(
              "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/publications/{document_id}/withdraw",
              {
                params: { path },
                body: { expected_version: audience.version },
              },
            );
          }}
          onSuccess={() => {
            onWithdraw();
            void refreshMemory(cache, account.id);
          }}
        />
      </div>
    </section>
  );
}
