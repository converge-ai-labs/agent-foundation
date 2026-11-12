import { Dictation } from "./dictation";
import type { DictationTarget } from "./dictation-context";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type RefObject,
} from "react";
import { Check, Trash } from "@phosphor-icons/react";
import { Button, Popover, PopoverPopup } from "a13n-ui";
import { useTranslation } from "../i18n";
import type { ThreadDraft } from "./draft";
import { AnnotationContext } from "./annotation-context";
import {
  quoteRange,
  selectedQuote,
  type ReplyAnnotation,
  type ReplyQuote,
} from "./reply-quote";
import styles from "./reply-annotations.module.css";

type Session = {
  stage: "selection" | "new" | "edit";
  quote: ReplyQuote;
  note?: ReplyAnnotation;
  element: HTMLElement;
  range?: Range;
  trigger?: HTMLElement;
};

export function ReplyAnnotations({
  draft,
  reader,
  dictation,
  enabled,
  children,
}: {
  draft: ThreadDraft;
  dictation?: DictationTarget;
  reader: RefObject<HTMLDivElement | null>;
  enabled: boolean;
  children: ReactNode;
}) {
  const notes = useSyncExternalStore(draft.subscribe, draft.getAnnotations);
  const [session, setSession] = useState<Session>();
  const editing = !!session && session.stage !== "selection";
  useEffect(() => {
    if (!enabled || editing) return;
    let dragging = false;
    let frame = 0;
    function read() {
      if (dragging || !reader.current) return;
      const candidate = selectedQuote(reader.current, window.getSelection());
      setSession((current) =>
        current && current.stage !== "selection"
          ? current
          : candidate
            ? { stage: "selection", ...candidate }
            : undefined,
      );
    }
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(read);
    };
    const down = (e: PointerEvent) => {
      dragging = !!reader.current?.contains(e.target as Node);
    };
    const up = () => {
      dragging = false;
      schedule();
    };
    document.addEventListener("pointerdown", down);
    document.addEventListener("pointerup", up);
    document.addEventListener("pointercancel", up);
    document.addEventListener("selectionchange", schedule);
    return () => {
      document.removeEventListener("pointerdown", down);
      document.removeEventListener("pointerup", up);
      document.removeEventListener("pointercancel", up);
      document.removeEventListener("selectionchange", schedule);
      cancelAnimationFrame(frame);
    };
  }, [reader, enabled, editing]);

  function edit(note: ReplyAnnotation, trigger: HTMLElement) {
    const root = reader.current;
    const key = CSS.escape(note.source);
    const element = root?.querySelector<HTMLElement>(
      `[data-reply-source="${key}"], [data-reply-slot="${key}"]`,
    );
    const marker = root?.querySelector<HTMLElement>(
      `[data-reply-marker="${CSS.escape(note.id)}"]`,
    );
    const range = element && quoteRange(element, note);
    if (marker?.getClientRects().length)
      marker.scrollIntoView({ block: "nearest", inline: "nearest" });
    const located = range && range.getClientRects().length && element;
    setSession({
      stage: "edit",
      note,
      quote: note,
      range: located ? range : undefined,
      element: located ? (marker ?? located) : trigger,
      trigger,
    });
  }
  function remove(id: string) {
    draft.setAnnotations(draft.annotations.filter((n) => n.id !== id));
    setSession(undefined);
  }
  function save(comment: string) {
    if (!session) return;
    const note = {
      ...session.quote,
      comment: comment.trim(),
      id: session.note?.id ?? crypto.randomUUID(),
    };
    draft.setAnnotations(
      session.note
        ? draft.annotations.map((n) => (n.id === note.id ? note : n))
        : [...draft.annotations, note],
    );
    setSession(undefined);
    requestAnimationFrame(() =>
      reader.current
        ?.querySelector<HTMLElement>(`[data-reply-marker="${note.id}"]`)
        ?.focus({ preventScroll: true }),
    );
  }
  return (
    <AnnotationContext
      value={
        enabled
          ? {
              notes,
              active: session?.note?.id,
              edit,
              remove,
              clear: () => {
                draft.setAnnotations([]);
                setSession(undefined);
              },
            }
          : null
      }
    >
      {children}
      {enabled && session && (
        <SelectionPopup
          key={session.note?.id ?? "new"}
          dictation={dictation}
          session={session}
          onStart={() => setSession({ ...session, stage: "new" })}
          onClose={() => setSession(undefined)}
          onSave={save}
          onDelete={() => session.note && remove(session.note.id)}
        />
      )}
    </AnnotationContext>
  );
}

function layoutComment(field: HTMLTextAreaElement) {
  const form = field.form;
  if (!form || form.dataset.stage !== "new") return;
  // Measure with the inline controls present so wrapping has a stable threshold.
  form.removeAttribute("data-multiline");
  form.toggleAttribute("data-multiline", field.scrollHeight > 24);
}

function SelectionPopup({
  dictation,
  session,
  onStart,
  onClose,
  onSave,
  onDelete,
}: {
  dictation?: DictationTarget;
  session: Session;
  onStart: () => void;
  onClose: () => void;
  onSave: (comment: string) => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const [dictating, setDictating] = useState(false);
  const [comment, setComment] = useState(session.note?.comment ?? "");
  const input = useRef<HTMLTextAreaElement>(null);
  const focusComment = useCallback((field: HTMLTextAreaElement | null) => {
    input.current = field;
    if (!field) return;
    field.focus({ preventScroll: true });
    const end = field.value.length;
    field.setSelectionRange(end, end);
    // The portal measures its final width after mount. A long, wrapped note
    // must show the insertion point as well as place it at the end.
    requestAnimationFrame(() => {
      if (
        document.activeElement === field &&
        field.selectionStart === end &&
        field.selectionEnd === end
      )
        field.scrollTop = field.scrollHeight;
    });
  }, []);
  const action = useRef<HTMLButtonElement>(null);
  const { stage, range, element, note } = session;
  const anchor = useMemo(
    () =>
      stage === "edit"
        ? element
        : {
            contextElement: element,
            getBoundingClientRect: () =>
              range?.getClientRects()[0] ?? element.getBoundingClientRect(),
          },
    [range, element, stage],
  );
  useLayoutEffect(() => {
    if (stage === "selection") {
      const focusAction = (event: KeyboardEvent) => {
        if (
          event.key === "Tab" &&
          !event.shiftKey &&
          document.activeElement !== action.current
        ) {
          event.preventDefault();
          action.current?.focus({ preventScroll: true });
        }
      };
      document.addEventListener("keydown", focusAction);
      return () => document.removeEventListener("keydown", focusAction);
    }
    if (range && typeof Highlight !== "undefined") {
      CSS.highlights.set("reply-annotation", new Highlight(range));
      window.getSelection()?.removeAllRanges();
      return () => CSS.highlights.delete("reply-annotation");
    }
  }, [stage, range, note]);
  // Dictation writes while the textarea is hidden. Measure only after the
  // updated text and the idle controls are both committed to the visible form.
  useLayoutEffect(() => {
    if (!dictating && input.current) layoutComment(input.current);
  }, [comment, dictating, stage]);
  const microphone = dictation && (
    <Dictation
      target={dictation}
      quote={session.quote}
      onActive={setDictating}
      insert={(text) => {
        const field = input.current;
        if (!field) return;
        field.setRangeText(
          text,
          field.selectionStart,
          field.selectionEnd,
          "end",
        );
        setComment(field.value);
        requestAnimationFrame(() => field.focus());
      }}
    />
  );
  return (
    <Popover
      key={stage}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <PopoverPopup
        anchor={anchor}
        side="top"
        align={stage === "edit" && range ? "center" : "start"}
        sideOffset={6}
        initialFocus={false}
        finalFocus={() => session.trigger ?? false}
        aria-label={stage === "selection" ? t("Add to chat") : t("Annotation")}
        className={stage === "selection" ? styles.selection : styles.editor}
      >
        {stage === "selection" ? (
          <Button
            variant="ghost"
            size="xs"
            ref={action}
            className={styles.add}
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={onStart}
          >
            {t("Add to chat")}
          </Button>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!dictating) onSave(input.current?.value ?? "");
            }}
            data-dictating={dictating || undefined}
            data-stage={stage}
            data-voice={dictation ? "" : undefined}
            className={styles.form}
          >
            {!range && (
              <blockquote className={styles.fallbackQuote}>
                {session.quote.text}
              </blockquote>
            )}
            <textarea
              key={note?.id ?? "new"}
              ref={focusComment}
              value={comment}
              onChange={(e) => setComment(e.currentTarget.value)}
              rows={1}
              aria-label={t("Annotation comment")}
              placeholder={t("Optional comment…")}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
            />
            {stage === "edit" ? (
              <div className={styles.actions}>
                <Button
                  type="button"
                  size="icon-xs"
                  variant="ghost"
                  aria-label={t("Delete annotation")}
                  onClick={onDelete}
                >
                  <Trash />
                </Button>
                {microphone}
                <Button
                  type="button"
                  size="xs"
                  variant="ghost"
                  onClick={onClose}
                >
                  {t("Cancel")}
                </Button>
                <Button type="submit" size="xs">
                  {t("Save")}
                </Button>
              </div>
            ) : (
              <>
                {microphone}
                <Button
                  className={styles.addNote}
                  type="submit"
                  size="icon-xs"
                  aria-label={t("Add annotation")}
                >
                  <Check weight="bold" aria-hidden="true" />
                </Button>
              </>
            )}
          </form>
        )}
      </PopoverPopup>
    </Popover>
  );
}
