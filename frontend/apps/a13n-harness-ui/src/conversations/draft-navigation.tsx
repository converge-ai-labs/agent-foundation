import { useTranslation } from "../i18n";
import { useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import {
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
  PopoverDescription,
} from "a13n-ui";
import { PencilSimple, WarningCircle } from "@phosphor-icons/react";
import { ErrorNotice } from "../shell/ui";
import { conversationTitle } from "./local-input";
import { useUnsent } from "./unsent";
import styles from "./conversation.module.css";
import draftStyles from "./draft-navigation.module.css";

export function DraftNavigation() {
  const { t } = useTranslation();
  const { rows, loading, error, retry } = useUnsent();
  const [open, setOpen] = useState(false);
  const openingDraft = useRef(false);
  const location = useLocation();
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) openingDraft.current = false;
        setOpen(next);
      }}
    >
      <PopoverTrigger
        className={styles.draftsTrigger}
        aria-label={
          error
            ? t("Drafts unavailable")
            : rows.length
              ? t("Drafts {{count, number}}", { count: rows.length })
              : t("Drafts")
        }
      >
        <PencilSimple size={18} aria-hidden="true" />
        <span>{t("Drafts")}</span>
        <span className={styles.draftsCount}>
          {error ? (
            <WarningCircle aria-label={t("Draft discovery unavailable")} />
          ) : rows.length > 0 ? (
            t("{{count, number}}", { count: rows.length })
          ) : null}
        </span>
      </PopoverTrigger>
      <PopoverPopup
        align="start"
        className={`${styles.draftsPopup} ${draftStyles.popup}`}
        finalFocus={() => !openingDraft.current}
      >
        <PopoverTitle className={styles.draftsTitle}>
          {t("Drafts")}
        </PopoverTitle>
        <PopoverDescription className={styles.draftsDescription}>
          {t("Shared, unsent input across projects.")}
        </PopoverDescription>
        <ErrorNotice error={error} retry={retry} />
        {rows.length > 0 ? (
          <ul
            className={`${styles.draftsList} ${draftStyles.list} a13n-scrollbar`}
          >
            {rows.map((row) => {
              const path = `/threads/${encodeURIComponent(row.thread.thread_id)}`;
              const title = conversationTitle(
                row.thread,
                [],
                t("Untitled chat"),
              );
              return (
                <li key={row.thread.thread_id}>
                  <Link
                    to={`${path}?compose=1`}
                    className={`${styles.draftShortcut} ${draftStyles.link}`}
                    onClick={(event) => {
                      if (
                        event.metaKey ||
                        event.ctrlKey ||
                        event.shiftKey ||
                        event.altKey ||
                        event.button !== 0
                      )
                        return;
                      openingDraft.current = true;
                      setOpen(false);
                      // Reopening the current draft need not remount its editor.
                      if (location.pathname === path)
                        requestAnimationFrame(() =>
                          document
                            .querySelector<HTMLElement>(
                              "[data-composer-editor]",
                            )
                            ?.focus(),
                        );
                    }}
                  >
                    <span title={title}>{title}</span>
                    <small>
                      {row.project_name ??
                        (row.thread.configuration.project_id
                          ? t("Unavailable project")
                          : t("Without a project"))}
                    </small>
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : !error ? (
          <p className={styles.draftsEmpty} role="status">
            {loading ? t("Loading drafts…") : t("No unsent drafts.")}
          </p>
        ) : null}
      </PopoverPopup>
    </Popover>
  );
}
