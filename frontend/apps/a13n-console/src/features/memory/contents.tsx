import { ArrowClockwiseIcon, PlusIcon } from "@phosphor-icons/react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Badge, Button, DisclosureSection, FormField, Input } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { ResourceTable } from "../../shared/collection";
import { ResourceReference } from "../../shared/identity";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import type { Schema } from "../../shared/api";
import { memoryApi, memoryKey, type MemoryTarget } from "./api";
import { MemoryRecordEditor } from "./record-editor";

const pageSize = 20;

export function MemoryContents({
  target,
  onEditing,
}: {
  target: MemoryTarget;
  onEditing: (value: boolean) => void;
}) {
  const { t } = useTranslation(),
    client = useClient();
  const api = memoryApi(client, target),
    key = memoryKey(target);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(20);
  const [threshold, setThreshold] = useState("");
  const [search, setSearch] = useState<Schema["MemorySearch"] | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [page, setPage] = useState(0);
  const [editor, setEditor] = useState<{ id?: string } | null>(null);
  const finalFocus = useRef<HTMLElement | null>(null);
  const access = useQuery({
    queryKey: [...key, "access"],
    queryFn: ({ signal }) => api.access(signal),
    refetchOnWindowFocus: "always",
    retry: false,
  });
  const list = useInfiniteQuery({
    queryKey: [...key, "list"],
    initialPageParam: undefined as string | undefined,
    enabled: access.isSuccess,
    queryFn: ({ signal, pageParam }) => api.list(signal, pageParam),
    getNextPageParam: (last) => last.pagination?.next_cursor ?? undefined,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const results = useQuery({
    queryKey: [...key, "search", search],
    enabled: access.isSuccess && !!search,
    queryFn: ({ signal }) => api.search(search!, signal),
    refetchOnWindowFocus: false,
    retry: false,
  });
  const records = search
    ? (results.data?.items ?? [])
    : [
        ...new Map(
          list.data?.pages
            .flatMap((part) => part.items)
            .map((item) => [item.id, item]) ?? [],
        ).values(),
      ];
  const pageCount = Math.max(1, Math.ceil(records.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const last = list.data?.pages.at(-1);
  const bounded = list.data?.pages.some((part) => part.pagination == null);
  const active = search ? results : list;
  const readOnly = !!access.error || !access.data?.can_write;
  function open(id?: string, element?: HTMLElement) {
    finalFocus.current = element ?? null;
    setEditor({ id });
    onEditing(true);
  }
  function close() {
    setEditor(null);
    onEditing(false);
    requestAnimationFrame(() => finalFocus.current?.focus());
  }
  if (access.isPending) return <Loading variant="table" rows={4} />;
  if (access.error && !editor)
    return (
      <ErrorNotice error={access.error} retry={() => void access.refetch()} />
    );
  return (
    <section
      className="flex min-w-0 flex-col gap-4"
      aria-label={t("Memory records")}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-base font-medium">{t("Memory records")}</h2>
          {readOnly && <Badge variant="secondary">{t("Read only")}</Badge>}
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={active.isFetching}
            onClick={() => {
              void access.refetch();
              void active.refetch();
            }}
          >
            <ArrowClockwiseIcon aria-hidden="true" />
            {t("Refresh")}
          </Button>
          {!readOnly && (
            <Button
              size="sm"
              onClick={(event) => open(undefined, event.currentTarget)}
            >
              <PlusIcon aria-hidden="true" />
              {t("Add memory")}
            </Button>
          )}
        </div>
      </div>
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (query.trim()) {
            const next = {
              query,
              limit,
              threshold: threshold === "" ? null : Number(threshold),
            };
            setSearch(next);
            setPage(0);
            if (JSON.stringify(search) === JSON.stringify(next))
              void results.refetch();
          }
        }}
      >
        <div className="flex flex-wrap items-end gap-2">
          <FormField
            className="min-w-0 flex-1 sm:max-w-sm"
            label={t("Semantic search")}
            hideLabel
          >
            <Input
              type="search"
              maxLength={16000}
              placeholder={t("Search memories by meaning…")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </FormField>
          <Button
            type="submit"
            variant="outline"
            disabled={!query.trim() || results.isFetching}
          >
            {t("Search")}
          </Button>
          {search && (
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setSearch(null);
                setQuery("");
                setPage(0);
              }}
            >
              {t("Back to list")}
            </Button>
          )}
        </div>
        <DisclosureSection
          title={t("Search options")}
          open={advanced}
          onOpenChange={setAdvanced}
        >
          <div
            className="grid gap-4 sm:grid-cols-2"
            onInvalidCapture={() => setAdvanced(true)}
          >
            <FormField label={t("Result limit")}>
              <Input
                type="number"
                required
                min={1}
                max={100}
                step={1}
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value))}
              />
            </FormField>
            <FormField
              label={t("Similarity threshold")}
              description={t(
                "Optional, from 0 to 1. Leave empty to use the backend default.",
              )}
            >
              <Input
                type="number"
                min={0}
                max={1}
                step="any"
                value={threshold}
                onChange={(event) => setThreshold(event.target.value)}
              />
            </FormField>
          </div>
        </DisclosureSection>
      </form>
      {search && (
        <p className="break-words text-sm text-muted-foreground">
          {t(
            "Search results for “{{query}}”. These are ranked matches, not the full collection.",
            { query: search.query },
          )}
        </p>
      )}
      <ErrorNotice error={access.error} retry={() => void access.refetch()} />
      <ErrorNotice error={active.error} retry={() => void active.refetch()} />
      {active.isPending ? (
        <Loading variant="table" rows={4} />
      ) : active.data ? (
        <>
          <div
            className="flex flex-wrap justify-between gap-2 text-sm text-muted-foreground"
            aria-live="polite"
          >
            <span>
              {t("{{count}} records loaded", { count: records.length })}
            </span>
            {!search && (
              <span>
                {t(
                  bounded
                    ? "Bounded list · not a total count"
                    : last?.pagination?.next_cursor
                      ? "More records available"
                      : "End of this traversal",
                )}
              </span>
            )}
          </div>
          {!records.length ? (
            <Empty
              title={t(search ? "No matching memories" : "No memories loaded")}
              description={t(
                search
                  ? "Try another query or lower the similarity threshold."
                  : bounded
                    ? "The backend returned a bounded result. This does not prove the collection is empty."
                    : "No records were returned for this provider and subject.",
              )}
            />
          ) : (
            <ResourceTable
              items={records.slice(
                currentPage * pageSize,
                (currentPage + 1) * pageSize,
              )}
              caption={t("Memory records")}
              onRowActivate={(item, element) => open(item.id, element)}
              columns={[
                {
                  label: t("Memory"),
                  tone: "primary",
                  render: (item) => (
                    <div className="flex min-w-0 max-w-3xl items-start gap-2">
                      <p className="line-clamp-3 min-w-0 whitespace-pre-wrap break-words">
                        {item.memory}
                      </p>
                      <ResourceReference id={item.id} />
                    </div>
                  ),
                },
                ...(search
                  ? [
                      {
                        label: t("Score"),
                        align: "right" as const,
                        render: (item: Schema["Memory"]) =>
                          item.score == null
                            ? "—"
                            : new Intl.NumberFormat(undefined, {
                                maximumFractionDigits: 3,
                              }).format(item.score),
                      },
                    ]
                  : []),
                {
                  label: t("Actions"),
                  align: "right",
                  render: (item) => (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={(event) => open(item.id, event.currentTarget)}
                    >
                      {t(readOnly ? "View" : "Edit")}
                    </Button>
                  ),
                },
              ]}
            />
          )}
          <div className="flex flex-wrap items-center justify-end gap-2">
            {pageCount > 1 && (
              <>
                <span className="mr-auto text-sm text-muted-foreground">
                  {t("Page {{page}} of {{pages}} loaded", {
                    page: currentPage + 1,
                    pages: pageCount,
                  })}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage === 0}
                  onClick={() => setPage(currentPage - 1)}
                >
                  {t("Previous")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage + 1 >= pageCount}
                  onClick={() => setPage(currentPage + 1)}
                >
                  {t("Next")}
                </Button>
              </>
            )}
            {!search && list.hasNextPage && (
              <Button
                size="sm"
                variant="outline"
                disabled={list.isFetching}
                onClick={() => void list.fetchNextPage()}
              >
                {t(list.isFetchingNextPage ? "Loading…" : "Load more records")}
              </Button>
            )}
          </div>
        </>
      ) : null}
      {editor && (
        <MemoryRecordEditor
          target={target}
          recordId={editor.id}
          canWrite={!readOnly}
          accessError={access.error}
          retryAccess={() => void access.refetch()}
          onClose={close}
        />
      )}
    </section>
  );
}
