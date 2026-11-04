import { useTranslation } from "../i18n";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import { Globe } from "@phosphor-icons/react";
import { useLiveThread } from "../conversations/live-threads";
import { readPreference, writePreference } from "../shell/preferences";

// Mounted only for the foreground Thread. Existing activity is a baseline, not a notification.
export function BrowserActivity({
  threadId,
  occupied,
  visible,
  reveal,
  open,
}: {
  threadId: string;
  occupied: boolean;
  visible: boolean;
  reveal: () => void;
  open: () => void;
}) {
  const { t } = useTranslation();
  const { display } = useLiveThread(threadId);
  const activity = display.browserActivityRunId;
  const observed = useRef(activity);
  const preferenceKey = `browser-revealed.${threadId}`;
  const revealed = useRef(readPreference(preferenceKey, "false") === "true");
  const markRevealed = useCallback(() => {
    revealed.current = true;
    writePreference(preferenceKey, "true");
  }, [preferenceKey]);
  const [suggested, setSuggested] = useState(false);
  useEffect(() => {
    if (!activity || observed.current === activity) return;
    observed.current = activity;
    if (revealed.current || readPreference(preferenceKey, "false") === "true")
      return;
    if (visible) {
      markRevealed();
      return;
    }
    if (occupied || window.matchMedia("(max-width: 999px)").matches) {
      setSuggested(true);
    } else {
      // Do not move keyboard focus or replace the current document.
      markRevealed();
      reveal();
    }
  }, [activity, occupied, visible, reveal, preferenceKey, markRevealed]);
  useEffect(() => {
    if (visible && suggested) {
      markRevealed();
      setSuggested(false);
    }
  }, [visible, suggested, markRevealed]);
  if (!suggested || visible) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => {
        setSuggested(false);
        markRevealed();
        open();
      }}
    >
      <Globe />
      {t("View browser")}
    </Button>
  );
}
