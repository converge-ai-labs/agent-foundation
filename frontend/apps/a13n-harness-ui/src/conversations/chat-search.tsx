import { useRef, useState } from "react";
import { Link } from "react-router";
import {
  Button,
  Dialog,
  DialogClose,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
  Input,
  Combobox,
  ComboboxTrigger,
  ComboboxPopup,
  ComboboxInput,
  ComboboxList,
  ComboboxItem,
  ComboboxEmpty,
} from "a13n-ui";
import { MagnifyingGlass, Funnel, X } from "@phosphor-icons/react";
import { useProjects } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { useThreads } from "./queries";
import { conversationTitle } from "./local-input";
import navigation from "./conversation.module.css";
import styles from "./chat-search.module.css";

export function ChatSearch({ onOpen }: { onOpen: () => void }) {
  const projects = useProjects();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [scope, setScope] = useState("");
  const projectOptions = [
    { value: "", label: "All projects" },
    ...(projects.data ?? []).map((project) => ({
      value: project.project_id,
      label: project.name,
    })),
  ];
  const input = useRef<HTMLInputElement>(null);
  const results = useRef<HTMLDivElement>(null);
  const close = () => {
    setOpen(false);
    setFilterOpen(false);
    setQuery("");
    setScope("");
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        if (value) onOpen();
        if (!value) {
          setFilterOpen(false);
          setQuery("");
          setScope("");
        }
      }}
    >
      <DialogTrigger className={navigation.homeLink} aria-label="Search chats">
        <MagnifyingGlass size={18} aria-hidden="true" />
        <span>Search</span>
      </DialogTrigger>
      <DialogPopup
        placement="top"
        showCloseButton={false}
        className={styles.popup}
        initialFocus={input}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          const links = [
            ...(results.current?.querySelectorAll<HTMLAnchorElement>("a") ??
              []),
          ];
          if (!links.length) return;
          const index = links.findIndex(
            (link) => link === document.activeElement,
          );
          if (document.activeElement !== input.current && index < 0) return;
          event.preventDefault();
          const next = index + (event.key === "ArrowDown" ? 1 : -1);
          if (next < 0) input.current?.focus();
          else links[Math.min(next, links.length - 1)]?.focus();
        }}
      >
        <DialogTitle className={navigation.srOnly}>Search chats</DialogTitle>
        <div className={styles.search}>
          <MagnifyingGlass size={20} aria-hidden="true" />
          <Input
            unstyled
            ref={input}
            aria-label="Find chats"
            type="search"
            placeholder="Search chats…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Combobox
            items={projectOptions}
            value={
              projectOptions.find((option) => option.value === scope) ??
              projectOptions[0]
            }
            itemToStringLabel={(option) => option.label}
            itemToStringValue={(option) => option.value}
            isItemEqualToValue={(option, selected) =>
              option.value === selected.value
            }
            open={filterOpen}
            onOpenChange={setFilterOpen}
            onValueChange={(option) => {
              if (!option) return;
              setScope(option.value);
              setFilterOpen(false);
              requestAnimationFrame(() => input.current?.focus());
            }}
          >
            <ComboboxTrigger
              render={<Button variant="ghost" size="icon" />}
              aria-label={
                scope
                  ? `Project scope: ${projectOptions.find((option) => option.value === scope)?.label ?? scope}`
                  : "Project scope"
              }
              className={scope ? styles.activeFilter : undefined}
            >
              <Funnel size={18} />
            </ComboboxTrigger>
            <ComboboxPopup align="end" className={styles.filter}>
              <ComboboxInput
                aria-label="Find project"
                placeholder="Find a project…"
                showTrigger={false}
              />
              <ComboboxEmpty>No matching projects.</ComboboxEmpty>
              <ComboboxList>
                {(option: { value: string; label: string }) => (
                  <ComboboxItem key={option.value} value={option}>
                    {option.label}
                  </ComboboxItem>
                )}
              </ComboboxList>
            </ComboboxPopup>
          </Combobox>
          <DialogClose
            render={<Button variant="ghost" size="icon" />}
            aria-label="Close search"
          >
            <X size={18} />
          </DialogClose>
        </div>
        {scope && (
          <div className={styles.scope}>
            <button
              type="button"
              onClick={() => {
                setScope("");
                input.current?.focus();
              }}
              aria-label="Clear project filter"
            >
              {projects.data?.find((project) => project.project_id === scope)
                ?.name ?? scope}
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        )}
        {open && query.trim() && (
          <div ref={results} className={`${styles.results} a13n-scrollbar`}>
            <SearchResults query={query.trim()} scope={scope} close={close} />
          </div>
        )}
      </DialogPopup>
    </Dialog>
  );
}

function SearchResults({
  query,
  scope,
  close,
}: {
  query: string;
  scope: string;
  close: () => void;
}) {
  const list = useThreads(query, scope || undefined);
  const rows = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.rows) ?? []).map((row) => [
        row.thread.thread_id,
        row,
      ]),
    ).values(),
  ];
  return (
    <section aria-label="Chat search results">
      {rows.map((row) => (
        <Link
          key={row.thread.thread_id}
          to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
          className={styles.result}
          aria-label={conversationTitle(row.thread)}
          onClick={close}
        >
          <span>
            <strong>{conversationTitle(row.thread)}</strong>
            <small>
              {row.project_name ?? "Without a project"}
              {row.thread.coordinator_thread_id ? " · Coordinator worker" : ""}
            </small>
          </span>
        </Link>
      ))}
      {list.isFetching && !list.isFetchingNextPage && (
        <p role="status" className={styles.empty}>
          {list.data ? "Updating results…" : "Searching chats…"}
        </p>
      )}
      {list.isSuccess && !rows.length && (
        <p className={styles.empty}>No matching chats.</p>
      )}
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.hasNextPage && (
        <Button
          variant="ghost"
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          Show more results
        </Button>
      )}
    </section>
  );
}
