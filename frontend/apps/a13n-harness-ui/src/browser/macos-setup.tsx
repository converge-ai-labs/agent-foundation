import { useState } from "react";
import { Button, FormField, Input } from "a13n-ui";
import { ErrorNotice } from "../shell/ui";
import { useTransport } from "../transport/context";
import styles from "./setup.module.css";

export function MacSharingSettings() {
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
        {pending ? "Opening…" : "Open Mac Sharing settings"}
      </Button>
      {opened && (
        <p role="status" className="text-xs text-muted-foreground">
          Settings opened on the Mac running Harness UI. After making changes,
          check again.
        </p>
      )}
      <ErrorNotice error={error} />
    </div>
  );
}

export function MacVncPassword({ onSaved }: { onSaved: () => void }) {
  const { client } = useTransport();
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const save = async () => {
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      await client.POST("/api/browser/macos/password", { body: { password } });
      setPassword("");
      setSaved(true);
      onSaved();
    } catch (error) {
      setError(error);
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="flex min-w-0 flex-col items-start gap-3">
      <div className={styles.passwordRow}>
        <FormField label="Mac VNC password" hideLabel>
          <Input
            type="password"
            autoComplete="new-password"
            value={password}
            disabled={pending}
            onChange={(event) => {
              setPassword(event.target.value);
              setSaved(false);
            }}
          />
        </FormField>
        <Button
          size="sm"
          variant="outline"
          disabled={pending || !password}
          onClick={() => void save()}
        >
          {pending ? "Verifying…" : "Verify and save password"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Enter your existing Screen Sharing password. Verified saves take effect
        without a restart.
      </p>
      {saved && (
        <p role="status" className="text-xs text-muted-foreground">
          Password verified and saved. No restart needed.
        </p>
      )}
      <ErrorNotice error={error} />
    </div>
  );
}
