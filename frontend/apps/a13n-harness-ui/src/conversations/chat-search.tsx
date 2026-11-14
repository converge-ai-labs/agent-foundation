import { useTranslation } from "../i18n";
import {
  Fragment,
  createContext,
  useContext,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link } from "react-router";
import {
  Button,
  Dialog,
  DialogClose,
  DialogPopup,
  DialogTitle,
  Input,
  Combobox,
  ComboboxTrigger,
  ComboboxPopup,
  ComboboxInput,
  ComboboxList,
  ComboboxItem,
  ComboboxEmpty,
} from "a13n-ui";
import { MagnifyingGlass, ChatCircle, Funnel, X } from "@phosphor-icons/react";
import { useProjects } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { fitVisualViewport } from "../shell/visual-viewport";
import { isFindShortcut } from "../platform/find-shortcut";
import { modifierLabel, usesCommandKey } from "../platform";
import { useThreads } from "./queries";
import { conversationTitle } from "./local-input";
import navigation from "./conversation.module.css";
import styles from "./chat-search.module.css";

const ChatSearchContext = createContext<{
  open: boolean;
  show: (from: HTMLElement) => void;
} | null>(null);

export function useChatSearchOpen() {
  return useContext(ChatSearchContext)?.open ?? false;
}

export function ChatSearch({ onOpen }: { onOpen: () => void }) {
  const { t } = useTranslation();
  const search = useContext(ChatSearchContext)!;
  const opened = useEffectEvent(onOpen);
  useEffect(() => {
    if (search.open) opened();
  }, [search.open]);
  return (
    <button
      type="button"
      className={navigation.homeLink}
      aria-label={t("Search chats")}
      aria-keyshortcuts={
        usesCommandKey() ? "Meta+F Meta+K" : "Control+F Control+K"
      }
      title={`${t("Search chats")} (${modifierLabel()}+K / ${modifierLabel()}+F)`}
      onClick={(event) => search.show(event.currentTarget)}
    >
      <MagnifyingGlass size={18} aria-hidden="true" />
      <span>{t("Search")}</span>
      <kbd className={styles.shortcut}>
        {`${modifierLabel()}${usesCommandKey() ? "" : " "}K`}
      </kbd>
    </button>
  );
}

export function ChatSearchProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const projects = useProjects();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [scope, setScope] = useState("");
  const projectOptions = [
    { value: "", label: t("All projects") },
    ...(projects.data ?? []).map((project) => ({
      value: project.project_id,
      label: project.name,
    })),
  ];
  const input = useRef<HTMLInputElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open && popup.current) return fitVisualViewport(popup.current);
  }, [open]);
  const results = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const show = (from: HTMLElement) => {
    returnFocus.current = from;
    setOpen(true);
  };
  useEffect(() => {
    const find = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null;
      const paletteShortcut =
        event.key.toLowerCase() === "k" &&
        (usesCommandKey()
          ? event.metaKey && !event.ctrlKey
          : event.ctrlKey && !event.metaKey) &&
        !event.altKey &&
        !event.shiftKey &&
        !event.isComposing;
      if (
        event.defaultPrevented ||
        (!isFindShortcut(event) && !paletteShortcut)
      )
        return;
      if (
        paletteShortcut &&
        (target?.closest(
          "[data-native-find], .xterm, [data-native-pane], [data-browser-pane], [data-desktop-pane]",
        ) ||
          (!usesCommandKey() &&
            target?.closest(
              'input, textarea, [contenteditable="true"], [role="textbox"]',
            )))
      )
        return;
      if (open) {
        event.preventDefault();
        setFilterOpen(false);
        input.current?.focus();
        input.current?.select();
        return;
      }
      if (
        document.querySelector('[role="dialog"]') ||
        (event.target instanceof Element &&
          event.target.closest("[data-native-find]"))
      )
        return;
      event.preventDefault();
      show(document.activeElement as HTMLElement);
    };
    // Local editors and terminals handle Find before this bubbling fallback.
    window.addEventListener("keydown", find);
    return () => window.removeEventListener("keydown", find);
  }, [open]);
  const close = () => {
    setOpen(false);
    setFilterOpen(false);
    setQuery("");
    setScope("");
  };
  return (
    <ChatSearchContext value={{ open, show }}>
      {children}
      <Dialog
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          if (!value) {
            setFilterOpen(false);
            setQuery("");
            setScope("");
          }
        }}
      >
        <DialogPopup
          ref={popup}
          placement="top"
          showCloseButton={false}
          className={styles.popup}
          initialFocus={input}
          finalFocus={() =>
            returnFocus.current?.isConnected &&
            !returnFocus.current.closest('[aria-hidden="true"], [inert]')
              ? returnFocus.current
              : document.querySelector<HTMLElement>(
                  '[aria-controls="workbench-navigation"]',
                )
          }
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
          <DialogTitle className={navigation.srOnly}>
            {t("Search chats")}
          </DialogTitle>
          <div className={styles.search}>
            <MagnifyingGlass size={20} aria-hidden="true" />
            <Input
              unstyled
              ref={input}
              aria-label={t("Find chats")}
              type="search"
              placeholder={t("Search chats…")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                // Enter opens the leading result, as the row hint shows.
                if (event.key !== "Enter" || event.nativeEvent.isComposing)
                  return;
                const first = results.current?.querySelector<HTMLElement>("a");
                if (!first) return;
                event.preventDefault();
                first.click();
              }}
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
                    ? t("Project scope: {{project}}", {
                        project:
                          projectOptions.find(
                            (option) => option.value === scope,
                          )?.label ?? scope,
                      })
                    : t("Project scope")
                }
                className={scope ? styles.activeFilter : undefined}
              >
                <Funnel size={18} />
              </ComboboxTrigger>
              <ComboboxPopup align="end" className={styles.filter}>
                <ComboboxInput
                  aria-label={t("Find project")}
                  placeholder={t("Find a project…")}
                  showTrigger={false}
                />
                <ComboboxEmpty>{t("No matching projects.")}</ComboboxEmpty>
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
              aria-label={t("Close search")}
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
                aria-label={t("Clear project filter")}
              >
                {projects.data?.find((project) => project.project_id === scope)
                  ?.name ?? scope}
                <X size={12} aria-hidden="true" />
              </button>
            </div>
          )}
          {open && (
            <div ref={results} className={`${styles.results} a13n-scrollbar`}>
              <SearchResults query={query.trim()} scope={scope} close={close} />
            </div>
          )}
          <footer className={styles.hints} aria-hidden="true">
            <span>
              <kbd>↵</kbd> {t("Open")}
            </span>
            <span>
              <kbd>↑↓</kbd> {t("Move")}
            </span>
            <span>
              <kbd>{t("Esc")}</kbd> {t("Close")}
            </span>
          </footer>
        </DialogPopup>
      </Dialog>
    </ChatSearchContext>
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
  const { t } = useTranslation();
  const list = useThreads(query, scope || undefined);
  const rows = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.rows) ?? []).map((row) => [
        row.thread.thread_id,
        row,
      ]),
    ).values(),
  ];
  const projectName = (row: (typeof rows)[number]) =>
    row.project_name ?? t("Without a project");
  // Recent chats keep recency order; search results group by Project.
  const grouped = query
    ? [...new Set(rows.map(projectName))].flatMap((name) =>
        rows.filter((row) => projectName(row) === name),
      )
    : rows;
  return (
    <section aria-label={t("Chat search results")}>
      {!query && <h2 className={styles.groupLabel}>{t("Recent")}</h2>}
      {grouped.map((row, index) => (
        <Fragment key={row.thread.thread_id}>
          {query &&
            (index === 0 ||
              projectName(grouped[index - 1]) !== projectName(row)) && (
              <h3 className={styles.groupLabel}>{projectName(row)}</h3>
            )}
          <Link
            key={row.thread.thread_id}
            to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
            className={styles.result}
            data-leading={index === 0 || undefined}
            aria-label={conversationTitle(row.thread, [], t("Untitled chat"))}
            onClick={close}
          >
            <ChatCircle size={18} aria-hidden="true" />
            <span>
              <strong>
                <MatchText
                  text={conversationTitle(row.thread, [], t("Untitled chat"))}
                  query={query}
                />
              </strong>
              {query ? (
                row.thread.coordinator_thread_id && (
                  <small>{t("Coordinator worker")}</small>
                )
              ) : (
                <small>
                  {row.thread.coordinator_thread_id
                    ? t("{{project}} - Coordinator worker", {
                        project: projectName(row),
                      })
                    : projectName(row)}
                </small>
              )}
            </span>
          </Link>
        </Fragment>
      ))}
      {list.isFetching && !list.isFetchingNextPage && (
        <p role="status" className={styles.empty}>
          {list.data ? t("Updating results…") : t("Searching chats…")}
        </p>
      )}
      {list.isSuccess && !rows.length && (
        <p className={styles.empty}>
          {query ? t("No matching chats.") : t("No chats yet")}
        </p>
      )}
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.hasNextPage && (
        <Button
          variant="ghost"
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          {t("Show more results")}
        </Button>
      )}
    </section>
  );
}

function MatchText({ text, query }: { text: string; query: string }) {
  const start = query
    ? text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase())
    : -1;
  if (start < 0) return text;
  return (
    <>
      {text.slice(0, start)}
      <mark>{text.slice(start, start + query.length)}</mark>
      {text.slice(start + query.length)}
    </>
  );
}
