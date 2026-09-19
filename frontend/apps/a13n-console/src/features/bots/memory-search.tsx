import { FileTextIcon, XIcon } from "@phosphor-icons/react";
import { Button, ChoiceField, Input } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import styles from "./bots.module.css";

function useMemoryQuery() {
  const [params, setParams] = useSearchParams();
  return {
    params,
    setParams,
    query: params.get("memory_query") ?? "",
    includeShared: params.get("memory_search_range") !== "local",
  };
}

/** Search field and range chip; they sit in the memory toolbar. */
export function MemorySearchField() {
  const { t } = useTranslation();
  const { params, setParams, query, includeShared } = useMemoryQuery();
  const [draft, setDraft] = useState(query);
  return (
    <form
      className={styles.memorySearch}
      onSubmit={(event) => {
        event.preventDefault();
        const next = new URLSearchParams(params);
        if (draft.trim()) next.set("memory_query", draft.trim());
        else next.delete("memory_query");
        setParams(next);
      }}
    >
      <Input
        type="search"
        size="sm"
        className={styles.searchInput}
        aria-label={t("Search memory")}
        placeholder={t("Search memory")}
        value={draft}
        maxLength={16000}
        onChange={(event) => setDraft(event.target.value)}
      />
      <Button
        type="submit"
        size="sm"
        variant="outline"
        disabled={!draft.trim()}
      >
        {t("Search")}
      </Button>
      {query && (
        <>
          <ChoiceField
            label={t("Search range")}
            variant="filter"
            value={includeShared ? "authorized" : "local"}
            options={[
              { value: "local", label: t("This group's own memory") },
              {
                value: "authorized",
                label: t("All memory available to this group"),
              },
            ]}
            onValueChange={(value) => {
              const next = new URLSearchParams(params);
              next.set("memory_search_range", value);
              setParams(next);
            }}
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              setDraft("");
              const next = new URLSearchParams(params);
              next.delete("memory_query");
              setParams(next);
            }}
          >
            <XIcon size={12} aria-hidden="true" />
            {t("Clear search")}
          </Button>
        </>
      )}
    </form>
  );
}

/** Result rows replace the document listing while a search is active. */
export function MemorySearchResults({
  accountId,
  scopeId,
  onSelect,
}: {
  accountId: string;
  scopeId: string;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const { params, query, includeShared } = useMemoryQuery();
  const result = useQuery({
    queryKey: ["bot-memory-search", accountId, scopeId, query, includeShared],
    enabled: !!query,
    queryFn: ({ signal }) =>
      client.http
        .POST(
          "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents/search",
          {
            params: { path: { account_id: accountId, scope_id: scopeId } },
            body: { query, include_shared: includeShared, limit: 20 },
            signal,
          },
        )
        .then(data),
  });
  if (!query) return null;
  return (
    <>
      <ErrorNotice error={result.error} retry={() => void result.refetch()} />
      {result.isPending || result.isFetching ? (
        <Loading variant="list" rows={3} />
      ) : (
        !result.error && (
          <>
            {result.data?.items.map((item) => (
              <button
                type="button"
                key={item.id}
                className={styles.documentItem}
                aria-pressed={params.get("memory_doc") === item.id}
                onClick={() => onSelect(item.id)}
              >
                <FileTextIcon aria-hidden="true" size={14} />
                <span>
                  <strong>{item.title}</strong>
                  <small>
                    {item.activity_date} ·{" "}
                    {t(item.shared ? "Shared with this group" : "Local memory")}
                  </small>
                </span>
              </button>
            ))}
            {!result.data?.items.length && (
              <p className={styles.listNote}>
                {t("No documents match this search.")}
              </p>
            )}
          </>
        )
      )}
      <p className={styles.listNote}>
        {t(
          "Search returns up to 20 relevant documents. Date and kind filters apply when browsing, not searching.",
        )}
      </p>
    </>
  );
}
