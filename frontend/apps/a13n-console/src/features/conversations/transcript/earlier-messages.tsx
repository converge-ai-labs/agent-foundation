import { Button } from "a13n-ui";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { useKeepPosition } from "./keep-position";

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
  const keepPosition = useKeepPosition(loadingEarlier);
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
            // The first message below the control keeps the reader's place.
            keepPosition(
              [
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
              ),
            );
            void loadEarlier();
          }}
        >
          {t("Load earlier messages")}
        </Button>
      )}
    </div>
  );
}
