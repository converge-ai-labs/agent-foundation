import { MagnifyingGlassIcon, FileTextIcon } from "@phosphor-icons/react";
import { Button, ChoiceField, Input } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import styles from "./bots.module.css";

export function MemorySearch({
  accountId,
  scopeId,
  onSelect,
}: {
  accountId: string;
  scopeId: string;
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    [params, setParams] = useSearchParams();
  const query = params.get("memory_query") ?? "";
  const includeShared = params.get("memory_search_range") !== "local";
  const [draft, setDraft] = useState(query);
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
  return (
    <div className={styles.memorySearch}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const next = new URLSearchParams(params);
          if (draft.trim()) next.set("memory_query", draft.trim());
          else next.delete("memory_query");
          setParams(next);
        }}
      >
        <div className={styles.searchInput}>
          <Input
            aria-label={t("Search memory")}
            placeholder={t("Search memory")}
            value={draft}
            maxLength={16000}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button
            type="submit"
            size="sm"
            variant="ghost"
            aria-label={t("Search")}
            disabled={!draft.trim()}
          >
            <MagnifyingGlassIcon aria-hidden="true" />
          </Button>
        </div>
        {query && (
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
            {t("Clear search")}
          </Button>
        )}
        {query && (
          <>
            <ChoiceField
              label={t("Search range")}
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
          </>
        )}
      </form>
      {query && (
        <>
          <p>
            {t(
              "Search returns up to 20 relevant documents. Date and kind filters apply when browsing, not searching.",
            )}
          </p>
          <ErrorNotice
            error={result.error}
            retry={() => void result.refetch()}
          />
          {result.isPending || result.isFetching ? (
            <Loading />
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
                    <FileTextIcon aria-hidden="true" />
                    <span>
                      <strong>{item.title}</strong>
                      <small>
                        {item.activity_date} ·{" "}
                        {t(
                          item.shared
                            ? "Shared with this group"
                            : "Local memory",
                        )}
                      </small>
                    </span>
                  </button>
                ))}
                {!result.data?.items.length && (
                  <p>{t("No documents match this search.")}</p>
                )}
              </>
            )
          )}
        </>
      )}
    </div>
  );
}
