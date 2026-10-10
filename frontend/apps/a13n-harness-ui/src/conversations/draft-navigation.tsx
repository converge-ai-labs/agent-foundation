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

export function DraftNavigation() {
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
            ? "Drafts unavailable"
            : rows.length
              ? `Drafts ${rows.length}`
              : "Drafts"
        }
      >
        <PencilSimple aria-hidden="true" />
        <span>Drafts</span>
        <span className={styles.draftsCount}>
          {error ? (
            <WarningCircle aria-label="Draft discovery unavailable" />
          ) : rows.length > 0 ? (
            rows.length
          ) : null}
        </span>
      </PopoverTrigger>
      <PopoverPopup
        align="start"
        className={styles.draftsPopup}
        finalFocus={() => !openingDraft.current}
      >
        <PopoverTitle className={styles.draftsTitle}>Drafts</PopoverTitle>
        <PopoverDescription className={styles.draftsDescription}>
          Shared, unsent input across projects.
        </PopoverDescription>
        <ErrorNotice error={error} retry={retry} />
        {rows.length > 0 ? (
          <ul className={`${styles.draftsList} a13n-scrollbar`}>
            {rows.map((row) => {
              const path = `/threads/${encodeURIComponent(row.thread.thread_id)}`;
              const title = conversationTitle(row.thread);
              return (
                <li key={row.thread.thread_id}>
                  <Link
                    to={`${path}?compose=1`}
                    className={styles.draftShortcut}
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
                          ? "Unavailable project"
                          : "Without a project")}
                    </small>
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : !error ? (
          <p className={styles.draftsEmpty} role="status">
            {loading ? "Loading drafts…" : "No unsent drafts."}
          </p>
        ) : null}
      </PopoverPopup>
    </Popover>
  );
}
