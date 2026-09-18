import {
  ArrowClockwiseIcon,
  PlusIcon,
  SlidersHorizontalIcon,
} from "@phosphor-icons/react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  Button,
  FormField,
  Input,
  MenuItem,
  Popover,
  PopoverPopup,
  PopoverTrigger,
  StatusPill,
} from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  Toolbar,
} from "../../shared/collection";
import { CopyableId } from "../../shared/identity";
import { ErrorNotice, Loading } from "../../shared/feedback";
import type { Schema } from "../../shared/api";
import { memoryApi, memoryKey, type MemoryTarget } from "./api";
import { MemoryRecordEditor } from "./record-editor";
import styles from "./memory.module.css";

const pageSize = 20;
/** A local step through the loaded window, never an opaque backend cursor. */
const localStep = "local";

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
  const [semantic, setSemantic] = useState(true);
  const [limit, setLimit] = useState(20);
  const [threshold, setThreshold] = useState("");
  const [search, setSearch] = useState<Schema["MemorySearch"] | null>(null);
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
  const loaded = search
    ? (results.data?.items ?? [])
    : [
        ...new Map(
          list.data?.pages
            .flatMap((part) => part.items)
            .map((item) => [item.id, item]) ?? [],
        ).values(),
      ];
  // Without the semantic backend the query filters what this window holds.
  const filter = !semantic && !search ? query.trim().toLocaleLowerCase() : "";
  const records = filter
    ? loaded.filter((item) => item.memory.toLocaleLowerCase().includes(filter))
    : loaded;
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(records.length / pageSize) - 1),
  );
  const last = list.data?.pages.at(-1);
  const bounded = list.data?.pages.some((part) => part.pagination == null);
  const active = search ? results : list;
  const readOnly = !!access.error || !access.data?.can_write;
  const moreLoaded = (currentPage + 1) * pageSize < records.length;
  const nextCursor =
    search || filter ? null : (last?.pagination?.next_cursor ?? null);
  const pager = {
    cursor: undefined as string | undefined,
    next: (step: string) => {
      setPage(currentPage + 1);
      if (step !== localStep) void list.fetchNextPage();
    },
    previous: currentPage > 0 ? () => setPage(currentPage - 1) : undefined,
    reset: () => setPage(0),
  };
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
    <section className={styles.records} aria-label={t("Memory records")}>
      <Toolbar
        trailing={
          <>
            {readOnly && (
              <StatusPill variant="neutral">{t("Read only")}</StatusPill>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={t("Refresh")}
              title={t("Refresh")}
              disabled={active.isFetching}
              onClick={() => {
                void access.refetch();
                void active.refetch();
              }}
            >
              <ArrowClockwiseIcon size={14} aria-hidden="true" />
            </Button>
            {!readOnly && (
              <Button
                variant="outline"
                size="sm"
                onClick={(event) => open(undefined, event.currentTarget)}
              >
                <PlusIcon aria-hidden="true" />
                {t("Add memory")}
              </Button>
            )}
          </>
        }
      >
        <form
          className={styles.searchForm}
          onSubmit={(event) => {
            event.preventDefault();
            if (!semantic || !query.trim()) return;
            const next = {
              query,
              limit: Math.min(100, Math.max(1, Math.round(limit) || 1)),
              threshold: threshold === "" ? null : Number(threshold),
            };
            setSearch(next);
            setPage(0);
            if (JSON.stringify(search) === JSON.stringify(next))
              void results.refetch();
          }}
        >
          <FormField
            className={styles.searchInput}
            label={t("Search memories")}
            hideLabel
          >
            <Input
              type="search"
              size="sm"
              maxLength={16000}
              placeholder={
                semantic
                  ? t("Search memories by meaning…")
                  : t("Filter loaded records…")
              }
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
            />
          </FormField>
          <Button
            type="button"
            size="sm"
            variant={semantic ? "secondary" : "outline"}
            aria-pressed={semantic}
            title={t(
              "Rank records by meaning in the backend instead of filtering the loaded window.",
            )}
            onClick={() => {
              setSemantic(!semantic);
              setSearch(null);
              setPage(0);
            }}
          >
            {t("Semantic")}
          </Button>
          {semantic && (
            <>
              <Button
                type="submit"
                size="sm"
                variant="outline"
                disabled={!query.trim() || results.isFetching}
              >
                {t("Search")}
              </Button>
              <Popover>
                <PopoverTrigger
                  render={
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t("Search options")}
                      title={t("Search options")}
                    />
                  }
                >
                  <SlidersHorizontalIcon size={14} aria-hidden="true" />
                </PopoverTrigger>
                <PopoverPopup align="start">
                  <div className={styles.options}>
                    <FormField label={t("Result limit")}>
                      <Input
                        type="number"
                        min={1}
                        max={100}
                        step={1}
                        value={limit}
                        onChange={(event) =>
                          setLimit(Number(event.target.value))
                        }
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
                </PopoverPopup>
              </Popover>
            </>
          )}
          {search && (
            <Button
              type="button"
              size="sm"
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
        </form>
      </Toolbar>
      {search && (
        <p className={styles.note}>
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
          {!records.length ? (
            <Empty
              title={t(
                filter
                  ? "No loaded records match"
                  : search
                    ? "No matching memories"
                    : "No memories loaded",
              )}
              description={t(
                filter
                  ? "This filters only the records already loaded here. Use Semantic to search the whole collection."
                  : search
                    ? "Try another query or lower the similarity threshold."
                    : bounded
                      ? "The backend returned a bounded result. This does not prove the collection is empty."
                      : "No records were returned for this provider and subject.",
              )}
            />
          ) : (
            <ResourceTable
              className={styles.recordsTable}
              items={records.slice(
                currentPage * pageSize,
                (currentPage + 1) * pageSize,
              )}
              caption={t("Memory records")}
              onRowActivate={(item, element) => open(item.id, element)}
              rowMenu={(item) => (
                <MenuItem onClick={() => open(item.id)}>
                  {t(readOnly ? "View" : "Edit")}
                </MenuItem>
              )}
              columns={[
                {
                  label: t("Memory"),
                  tone: "primary",
                  render: (item) => (
                    <p className={styles.excerpt}>{item.memory}</p>
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
                  label: t("Reference"),
                  tone: "muted",
                  align: "right",
                  render: (item) => <CopyableId value={item.id} />,
                },
              ]}
            />
          )}
          <CollectionFooter
            count={
              <span className={styles.count} aria-live="polite">
                <span>
                  {filter
                    ? t("{{count}} of {{total}} loaded records match", {
                        count: records.length,
                        total: loaded.length,
                      })
                    : t("{{count}} records loaded", { count: records.length })}
                </span>
                {!search && (
                  <span className={styles.footnote}>
                    {t(
                      bounded
                        ? "Bounded list · not a total count"
                        : nextCursor
                          ? "More records available"
                          : "End of this traversal",
                    )}
                  </span>
                )}
              </span>
            }
          >
            <Pagination
              page={pager}
              next={moreLoaded ? localStep : nextCursor}
            />
          </CollectionFooter>
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
