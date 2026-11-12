import { useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import { type EditorView, runScopeHandlers } from "@codemirror/view";
import {
  SearchQuery,
  closeSearchPanel,
  findNext,
  findPrevious,
  selectMatches,
  setSearchQuery,
} from "@codemirror/search";
import {
  Button,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  MenuCheckboxItem,
  MenuSeparator,
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
} from "a13n-ui";
import {
  CaretUp,
  CaretDown,
  SlidersHorizontal,
  X,
} from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { isFindShortcut } from "../platform/find-shortcut";
import styles from "./file-search.module.css";

export function FileSearch({
  view,
  query,
}: {
  view: EditorView;
  query: SearchQuery;
}) {
  const { t } = useTranslation();
  const field = useRef<HTMLInputElement>(null);
  const doc = view.state.doc;
  const hits = useMemo(() => {
    const matches: { from: number; to: number }[] = [];
    if (query.valid && query.search) {
      const cursor = query.getCursor(doc);
      while (matches.length <= 999) {
        const hit = cursor.next();
        if (hit.done) break;
        matches.push(hit.value);
      }
    }
    return matches;
  }, [doc, query]);
  const selection = view.state.selection.main;
  const active = hits.findIndex(
    (hit) => hit.from === selection.from && hit.to === selection.to,
  );
  useLayoutEffect(() => {
    field.current?.focus();
    field.current?.select();
  }, []);
  function change(
    patch: Partial<
      Pick<SearchQuery, "search" | "regexp" | "caseSensitive" | "wholeWord">
    >,
  ) {
    view.dispatch({
      effects: setSearchQuery.of(new SearchQuery({ ...query, ...patch })),
    });
  }
  function action(
    label: string,
    icon: ReactNode,
    click: () => void,
    expanded?: boolean,
  ) {
    return (
      <Tooltip>
        <TooltipTrigger
          render={<Button size="icon-sm" variant="ghost" />}
          aria-label={label}
          className={styles.icon}
          aria-expanded={expanded}
          onClick={click}
        >
          {icon}
        </TooltipTrigger>
        <TooltipPopup>{label}</TooltipPopup>
      </Tooltip>
    );
  }
  return (
    <div
      className={styles.search}
      role="search"
      aria-label={t("Find")}
      onKeyDown={(e) => {
        if (
          e.nativeEvent.isComposing ||
          !e.currentTarget.contains(e.target as Node)
        )
          return;
        if (isFindShortcut(e.nativeEvent)) {
          e.preventDefault();
          e.stopPropagation();
          field.current?.focus();
          field.current?.select();
        } else if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          closeSearchPanel(view);
          view.focus();
        } else if (e.key === "Enter" && e.target === field.current) {
          e.preventDefault();
          e.stopPropagation();
          (e.shiftKey ? findPrevious : findNext)(view);
        } else if (runScopeHandlers(view, e.nativeEvent, "search-panel")) {
          e.preventDefault();
          e.stopPropagation();
        }
      }}
    >
      <div className={styles.row}>
        <div className={styles.field}>
          <input
            ref={field}
            main-field="true"
            name="search"
            aria-label={t("Find")}
            placeholder={t("Find")}
            value={query.search}
            onChange={(e) => change({ search: e.target.value })}
          />
          {query.search && (
            <span className={styles.count} role="status">
              {active >= 0 && hits.length <= 999 ? active + 1 + " / " : ""}
              {hits.length > 999 ? "999+" : hits.length}
            </span>
          )}
          <Menu>
            <MenuTrigger
              render={
                <Button
                  size="icon-sm"
                  variant="ghost"
                  className={styles.icon}
                />
              }
              aria-label={t("Search options")}
              title={t("Search options")}
              data-active={
                query.caseSensitive ||
                query.regexp ||
                query.wholeWord ||
                undefined
              }
            >
              <SlidersHorizontal />
            </MenuTrigger>
            <MenuPopup align="end">
              {(
                [
                  ["caseSensitive", "Match case"],
                  ["wholeWord", "Whole word"],
                  ["regexp", "Regular expression"],
                ] as const
              ).map(([key, label]) => (
                <MenuCheckboxItem
                  key={key}
                  checked={query[key]}
                  onCheckedChange={(checked) => change({ [key]: checked })}
                >
                  {t(label)}
                </MenuCheckboxItem>
              ))}
              <MenuSeparator />
              <MenuItem
                disabled={!hits.length}
                onClick={() => selectMatches(view)}
              >
                {t("Select all matches")}
              </MenuItem>
            </MenuPopup>
          </Menu>
        </div>
        {action(t("Previous match"), <CaretUp />, () => findPrevious(view))}
        {action(t("Next match"), <CaretDown />, () => findNext(view))}
        {action(t("Close search"), <X />, () => closeSearchPanel(view))}
      </div>
    </div>
  );
}
