import { useState } from "react";
import { Button } from "a13n-ui";
import { ErrorNotice } from "../shell/ui";
import { useTransport } from "../transport/context";

export function MacCaptureSettings() {
  const { client } = useTransport();
  const [pending, setPending] = useState(false);
  const [opened, setOpened] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const open = async () => {
    setPending(true);
    setError(null);
    setOpened(false);
    try {
      await client.POST("/api/browser/macos/settings");
      setOpened(true);
    } catch (error) {
      setError(error);
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="flex flex-col items-start gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={pending}
        onClick={() => void open()}
      >
        {pending ? "Opening…" : "Open Mac Screen Recording settings"}
      </Button>
      {opened && (
        <p role="status" className="text-xs text-muted-foreground">
          Settings opened on the Mac running Harness UI. Allow Screen Recording
          for its launcher app, then reconnect. macOS may require restarting the
          app.
        </p>
      )}
      <ErrorNotice error={error} />
    </div>
  );
}
