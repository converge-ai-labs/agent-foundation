import { useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import { Globe } from "@phosphor-icons/react";
import { useLiveThread } from "../conversations/live-threads";

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
  const { display } = useLiveThread(threadId);
  const activity = display.browserActivityRunId;
  const observed = useRef(activity);
  const [suggested, setSuggested] = useState(false);
  useEffect(() => {
    if (!activity || observed.current === activity) return;
    observed.current = activity;
    if (visible) return;
    if (occupied || window.matchMedia("(max-width: 999px)").matches) {
      setSuggested(true);
    } else {
      // Do not move keyboard focus or replace the current document.
      reveal();
    }
  }, [activity, occupied, visible, reveal]);
  useEffect(() => {
    if (visible) setSuggested(false);
  }, [visible]);
  if (!suggested || visible) return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => {
        setSuggested(false);
        open();
      }}
    >
      <Globe /> View browser
    </Button>
  );
}
