import { Button } from "a13n-ui";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";

export function EarlierMessages({
  hasEarlier,
  loadingEarlier,
  earlierError,
  loadEarlier,
}: {
  hasEarlier: boolean;
  loadingEarlier: boolean;
  earlierError?: unknown;
  loadEarlier: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const root = useRef<HTMLDivElement>(null);
  const anchor = useRef<{
    element: Element;
    top: number;
    viewport: HTMLElement;
  } | null>(null);
  useLayoutEffect(() => {
    if (loadingEarlier || !anchor.current) return;
    const { element, top, viewport } = anchor.current;
    if (element.isConnected)
      viewport.scrollTop += element.getBoundingClientRect().top - top;
    delete viewport.dataset.loadingEarlier;
    viewport.dispatchEvent(new Event("scroll"));
    anchor.current = null;
  }, [loadingEarlier]);
  useLayoutEffect(
    () => () => {
      if (anchor.current) delete anchor.current.viewport.dataset.loadingEarlier;
    },
    [],
  );
  if (!hasEarlier && !earlierError) return null;
  return (
    <div ref={root}>
      <ErrorNotice error={earlierError} />
      {hasEarlier && (
        <Button
          size="sm"
          variant="outline"
          type="button"
          loading={loadingEarlier}
          onClick={() => {
            const control = root.current;
            const viewport = control?.closest("[data-session-stage]");
            const element = [
              ...(control?.parentElement?.querySelectorAll(
                "[data-message-id]",
              ) ?? []),
            ].find(
              (candidate) =>
                !!control &&
                !!(
                  control.compareDocumentPosition(candidate) &
                  Node.DOCUMENT_POSITION_FOLLOWING
                ),
            );
            if (element && viewport instanceof HTMLElement) {
              anchor.current = {
                element,
                top: element.getBoundingClientRect().top,
                viewport,
              };
              viewport.dataset.loadingEarlier = "true";
            }
            void loadEarlier();
          }}
        >
          {t("Load earlier messages")}
        </Button>
      )}
    </div>
  );
}
