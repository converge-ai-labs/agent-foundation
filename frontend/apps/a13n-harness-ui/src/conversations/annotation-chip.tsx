import { useContext, useRef, useState } from "react";
import { ChatTeardropText, PencilSimple, Trash } from "@phosphor-icons/react";
import { Button, Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { useTranslation } from "../i18n";
import { AnnotationContext } from "./annotation-context";
import styles from "./reply-annotations.module.css";
import chipRemove from "./chip-remove.module.css";

export function AnnotationChip() {
  const ctx = useContext(AnnotationContext);
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const openingEditor = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  if (!ctx?.notes.length) return null;
  const { notes, edit, remove } = ctx;
  return (
    <div className={styles.chipRow}>
      <Popover
        open={open}
        onOpenChange={(next) => {
          if (next) openingEditor.current = false;
          setOpen(next);
        }}
      >
        <span className={styles.chipGroup}>
          <PopoverTrigger
            render={<Button variant="ghost" size="xs" />}
            ref={trigger}
            openOnHover
            delay={120}
            closeDelay={220}
            className={styles.chip}
          >
            <ChatTeardropText aria-hidden />
            {t("Annotation count", { count: notes.length })}
          </PopoverTrigger>
          <button
            type="button"
            className={styles.clearChip}
            title={t("Clear annotations")}
            aria-label={t("Clear annotations")}
            onClick={() => {
              setOpen(false);
              ctx.clear();
            }}
          >
            <span className={chipRemove.face} aria-hidden />
          </button>
        </span>
        <PopoverPopup
          side="top"
          align="start"
          sideOffset={6}
          initialFocus={false}
          finalFocus={() => !openingEditor.current}
          className={styles.preview}
          aria-label={t("Annotations")}
        >
          <ol className="a13n-scrollbar">
            {notes.map((note, index) => (
              <li key={note.id}>
                <div className={styles.itemHeading}>
                  <span className={styles.ordinal}>{index + 1}.</span>
                  <span>{t("Selected text")}</span>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={t("Edit annotation {{number}}", {
                      number: index + 1,
                    })}
                    onClick={() => {
                      openingEditor.current = true;
                      setOpen(false);
                      if (trigger.current) edit(note, trigger.current);
                    }}
                  >
                    <PencilSimple />
                  </Button>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={t("Delete annotation {{number}}", {
                      number: index + 1,
                    })}
                    onClick={() => {
                      if (notes.length === 1) setOpen(false);
                      remove(note.id);
                    }}
                  >
                    <Trash />
                  </Button>
                </div>
                <blockquote>{note.text}</blockquote>
                {note.comment && (
                  <p>
                    <span className="sr-only">{t("Your comment")}: </span>
                    {note.comment}
                  </p>
                )}
              </li>
            ))}
          </ol>
        </PopoverPopup>
      </Popover>
    </div>
  );
}
