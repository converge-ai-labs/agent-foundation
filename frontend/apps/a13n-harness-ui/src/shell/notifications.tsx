import { SettingsRow, SettingsSection } from "./settings";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useMatch, useNavigate } from "react-router";
import { fitVisualViewport } from "./visual-viewport";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Switch, ToastProvider, useToast } from "a13n-ui";
import type { Schema } from "../transport/client";
import { readPreference, writePreference } from "./preferences";
import { TransportContext } from "../transport/context";
import {
  disablePush,
  enablePush,
  reportPushActivity,
  pushSubscriptionId,
  supportsPush,
  testPush,
} from "./push";
import { PageHeader } from "./ui";
import styles from "./notifications.module.css";

type Permission = NotificationPermission | "unavailable" | "install-required";
type Background = "disabled" | "checking" | "enabled" | "error";
type SummaryEvent = Schema<"SummaryInvalidation">;
const ENABLED = "notifications.enabled";
const PROMPT_DISMISSED = "notifications.prompt-dismissed";
export function notificationPermission(): Permission {
  if (!window.isSecureContext) return "unavailable";
  const appleMobile =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
  // Prefer capability detection; the platform hint only explains missing APIs.
  if (appleMobile && !standalone && !supportsPush()) return "install-required";
  return supportsPush() ? Notification.permission : "unavailable";
}

type Notifications = {
  enabled: boolean;
  permission: Permission;
  requesting: boolean;
  error: string;
  testStatus: string;
  background: Background;
  setEnabled: (value: boolean) => void;
  request: () => Promise<void>;
  test: () => void;
  receive: (event: SummaryEvent) => void;
};
const NotificationsContext = createContext<Notifications | null>(null);
export const useNotifications = () => useContext(NotificationsContext);

export function NotificationsProvider({ children }: { children: ReactNode }) {
  return (
    <ToastProvider closeLabel="Dismiss notification">
      <NotificationState>{children}</NotificationState>
    </ToastProvider>
  );
}
function NotificationState({ children }: { children: ReactNode }) {
  const frame = useRef<HTMLDivElement>(null);
  useEffect(
    () => (frame.current ? fitVisualViewport(frame.current) : undefined),
    [],
  );
  const [enabled, updateEnabled] = useState(
    () => readPreference(ENABLED, "true") !== "false",
  );
  const [promptDismissed, setPromptDismissed] = useState(
    () => readPreference(PROMPT_DISMISSED, "false") === "true",
  );
  const dismissPrompt = () => {
    writePreference(PROMPT_DISMISSED, "true");
    setPromptDismissed(true);
  };
  const [permission, setPermission] = useState(notificationPermission);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState("");
  const [testStatus, setTestStatus] = useState("");
  const [background, setBackground] = useState<Background>(() =>
    pushSubscriptionId() ? "checking" : "disabled",
  );
  const [refreshVersion, refreshSubscription] = useState(0);
  const transport = useContext(TransportContext);
  const navigate = useNavigate();
  const settings = useMatch("/settings/*");
  const viewedThread = useMatch("/threads/:threadId/*")?.params.threadId;
  const queries = useQueryClient();
  const toast = useToast();
  const seen = useRef(new Set<string>());
  const alive = useRef(false);
  const current = useRef({
    enabled,
    navigate,
    toast,
    background,
    viewedThread,
  });
  current.current = { enabled, navigate, toast, background, viewedThread };
  useEffect(() => {
    alive.current = true;
    const refresh = () => {
      setPermission(notificationPermission());
      if (document.visibilityState === "visible")
        refreshSubscription((version) => version + 1);
      updateEnabled(
        readPreference(ENABLED, String(current.current.enabled)) !== "false",
      );
    };
    window.addEventListener("focus", refresh);
    window.addEventListener("storage", refresh);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      alive.current = false;
      window.removeEventListener("focus", refresh);
      window.removeEventListener("storage", refresh);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);
  useEffect(() => {
    if (requesting) return;
    if (!pushSubscriptionId()) {
      setBackground("disabled");
      return;
    }
    if (!transport) return;
    let active = true;
    setBackground("checking");
    const sync =
      enabled && permission === "granted"
        ? enablePush(transport, false)
        : disablePush(transport);
    void sync
      .then(() => {
        if (active) {
          setBackground(pushSubscriptionId() ? "enabled" : "disabled");
          setError("");
        }
      })
      .catch(() => {
        if (active) {
          setBackground("error");
          setError(
            "Background alerts are paused. Reconnect to resume delivery.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [transport, enabled, permission, refreshVersion, requesting]);
  useEffect(() => {
    if (!transport || !enabled || permission !== "granted" || requesting)
      return;
    const controller = new AbortController();
    let pending = false;
    const pulse = () => {
      if (pending || document.visibilityState !== "visible") return;
      pending = true;
      void reportPushActivity(transport, controller.signal)
        .catch(() => {
          // Best effort: retry on the next visible pulse, never recreate a row.
        })
        .finally(() => {
          pending = false;
        });
    };
    pulse();
    const timer = window.setInterval(pulse, 60000);
    document.addEventListener("visibilitychange", pulse);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", pulse);
    };
  }, [transport, enabled, permission, requesting, background]);
  const setEnabled = (value: boolean) => {
    current.current.enabled = value;
    writePreference(ENABLED, String(value));
    updateEnabled(value);
    if (value) {
      writePreference(PROMPT_DISMISSED, "false");
      setPromptDismissed(false);
    }
    setPermission(notificationPermission());
    setError("");
    setTestStatus("");
    if (!value) {
      current.current.background = "disabled";
      setBackground("disabled");
      void disablePush(transport ?? undefined)
        .then(() => setBackground("disabled"))
        .catch(() => {
          setBackground("error");
          setError(
            "Could not confirm background notifications were disabled. Check browser site permissions and try again.",
          );
        });
    }
  };
  const request = async () => {
    if (
      requesting ||
      !transport ||
      ["denied", "unavailable", "install-required"].includes(
        notificationPermission(),
      )
    )
      return;
    setEnabled(true);
    setRequesting(true);
    try {
      await enablePush(transport);
      if (alive.current)
        setBackground(pushSubscriptionId() ? "enabled" : "disabled");
      if (alive.current) setPermission(notificationPermission());
    } catch (failure) {
      if (alive.current) {
        setBackground("error");
        setPermission(notificationPermission());
        setError(
          failure instanceof Error
            ? failure.message
            : "Notification setup failed. Check this site's browser settings.",
        );
      }
    } finally {
      if (alive.current) setRequesting(false);
    }
  };
  const receive = useCallback(
    (event: SummaryEvent) => {
      const notice = event.notice;
      const threadId = event.root_thread_id;
      if (
        !alive.current ||
        !notice ||
        !threadId ||
        event.kind !== "root_operation"
      )
        return;
      const id = `${event.epoch}:${notice.receipt_id}`;
      if (seen.current.has(id)) return;
      seen.current.add(id);
      if (seen.current.size > 256)
        seen.current.delete(seen.current.values().next().value!);
      // The transcript already shows completion. Keep receipt deduplication,
      // attention/failure notices and server Push delivery independent.
      if (
        (notice.status === "completed" || notice.status === "goal_verified") &&
        current.current.viewedThread === threadId
      )
        return;
      const detail = queries.getQueryData<Schema<"ThreadDetail">>([
        "thread",
        threadId,
        "detail",
      ]);
      const name =
        detail?.thread.title || detail?.thread.excerpt?.first_input || "Chat";
      const state = {
        completed: "Task completed",
        failed: "Task failed",
        suspended: "Your input is needed",
        goal_verified: "Goal verified by the Agent",
        goal_incomplete: "Goal not verified",
      }[notice.status];
      const title = `${state} · ${Array.from(name).slice(0, 80).join("")}`;
      const path = `/threads/${encodeURIComponent(threadId)}`;
      current.current.toast.add({
        id,
        title,
        description: notice.brief,
        type:
          notice.status === "failed"
            ? "error"
            : notice.status === "suspended" ||
                notice.status === "goal_incomplete"
              ? "warning"
              : "success",
        timeout: notice.status === "suspended" ? 0 : 8000,
        actionProps: {
          children: (
            <>
              <span className={styles.desktopLabel}>Open chat</span>
              <span className={styles.mobileLabel}>Open</span>
            </>
          ),
          "aria-label": "Open chat",
          onClick: () => current.current.navigate(path),
        },
      });
    },
    [queries],
  );
  const test = () => {
    setError("");
    setTestStatus("");
    if (background === "enabled" && pushSubscriptionId() && transport) {
      setTestStatus("Sending through the background push service…");
      void testPush(transport)
        .then(() => {
          if (alive.current)
            setTestStatus(
              "Test accepted by the push service; delivery is not confirmed. Check your device for the notification.",
            );
        })
        .catch((failure: unknown) => {
          if (!alive.current) return;
          setTestStatus("");
          setBackground("error");
          setError(
            failure instanceof Error
              ? failure.message
              : "Background notification test failed.",
          );
        });
      return;
    }
  };
  const value = {
    enabled,
    permission,
    requesting,
    error,
    testStatus,
    background,
    setEnabled,
    request,
    test,
    receive,
  };
  return (
    <NotificationsContext value={value}>
      <div className={styles.frame} ref={frame}>
        {children}
        {enabled &&
          !promptDismissed &&
          !settings &&
          !requesting &&
          background !== "checking" &&
          (permission !== "granted" ||
            (supportsPush() &&
              (background === "disabled" || background === "error"))) && (
            <PermissionToast onDismiss={dismissPrompt} />
          )}
      </div>
    </NotificationsContext>
  );
}

const permissionLabels: Record<Permission, string> = {
  default: "Not yet allowed",
  granted: "Allowed",
  denied: "Blocked by browser",
  unavailable: "Unavailable here",
  "install-required": "Open from Home Screen",
};
const backgroundLabels: Record<Background, string> = {
  disabled: "Not enabled",
  checking: "Checking subscription…",
  enabled: "Enabled on this device",
  error: "Subscription needs attention",
};
function PermissionDescription({ permission }: { permission: Permission }) {
  return (
    <>
      {permission === "install-required"
        ? "Add Harness to your Home Screen and open it there to enable alerts. Requires iOS or iPadOS 16.4 or later."
        : permission === "denied"
          ? "Allow notifications in this site's browser settings or the Home Screen app's system settings."
          : permission === "unavailable"
            ? "Use a supported browser on HTTPS or localhost. In-app notices remain available."
            : "Get alerts when an agent finishes, fails, or needs your input."}
    </>
  );
}
function PermissionToast({ onDismiss }: { onDismiss: () => void }) {
  const notifications = useNotifications()!;
  const toast = useToast();
  const navigate = useNavigate();
  const latest = useRef({ onDismiss, navigate, toast });
  latest.current = { onDismiss, navigate, toast };
  const issue = notifications.background === "error";
  useEffect(() => {
    let active = true;
    const manager = latest.current.toast;
    const id = manager.add({
      id: "notification-setup",
      title: issue ? "Background alerts paused" : "Get task notifications",
      description: issue
        ? "Alerts still appear here. Reconnect in Settings."
        : "Get notified when a task finishes or needs your input.",
      type: issue ? "warning" : "info",
      timeout: 8000,
      actionProps: {
        children: "Settings",
        onClick: () => {
          latest.current.onDismiss();
          latest.current.navigate("/settings/notifications");
        },
      },
      onClose: () => {
        if (active) latest.current.onDismiss();
      },
    });
    return () => {
      active = false;
      manager.close(id);
    };
  }, [issue]);
  return null;
}
export function NotificationSettings({
  embedded = false,
}: {
  embedded?: boolean;
}) {
  const notifications = useNotifications()!;
  const pushReady =
    notifications.enabled &&
    notifications.permission === "granted" &&
    supportsPush();
  const backgroundLabel = pushSubscriptionId()
    ? "Reconnect background notifications"
    : "Enable background notifications";
  return (
    <div className={styles.settings}>
      {!embedded && (
        <PageHeader
          title="Notifications"
          description="Task results and requests for your input on this browser."
        />
      )}
      <SettingsSection
        variant="grouped"
        title={embedded ? "Notifications" : undefined}
      >
        <SettingsRow
          label="Enable browser notifications"
          stackOnNarrow={false}
          controlId="desktop-notifications"
          description="Alerts from every chat in this browser. Previews may appear on your lock screen."
        >
          <Switch
            id="desktop-notifications"
            checked={notifications.enabled}
            onCheckedChange={notifications.setEnabled}
          />
        </SettingsRow>
        <SettingsRow
          label="Browser permission"
          description={
            notifications.permission !== "granted" && (
              <PermissionDescription permission={notifications.permission} />
            )
          }
        >
          <div className={styles.state}>
            <span className={styles.status}>
              {permissionLabels[notifications.permission]}
            </span>
            {notifications.enabled &&
              notifications.permission === "default" && (
                <Button
                  variant="outline"
                  onClick={() => void notifications.request()}
                  loading={notifications.requesting}
                >
                  Enable alerts
                </Button>
              )}
          </div>
        </SettingsRow>
        <SettingsRow
          label="Background delivery"
          description={
            notifications.background === "enabled"
              ? "Alerts stay active for six hours after your last visit on this device."
              : notifications.permission !== "granted"
                ? "Needs notification permission. In-app notices remain available."
                : "Without working push delivery, only in-app notices are available while WebUI is open."
          }
        >
          <div className={styles.state}>
            <span className={styles.status}>
              {backgroundLabels[notifications.background]}
            </span>
            {pushReady && (
              <Button
                onClick={() => void notifications.request()}
                loading={notifications.requesting}
                variant={
                  notifications.background === "enabled" ? "outline" : "default"
                }
                aria-label={backgroundLabel}
              >
                {pushSubscriptionId() ? "Reconnect" : "Enable"}
              </Button>
            )}
          </div>
        </SettingsRow>
        <SettingsRow
          label="Test notification"
          description="Send an alert to check delivery on this device."
        >
          <Button
            variant="outline"
            aria-label="Send test notification"
            disabled={
              !notifications.enabled ||
              notifications.permission !== "granted" ||
              notifications.requesting ||
              notifications.background !== "enabled"
            }
            onClick={notifications.test}
          >
            Send test
          </Button>
        </SettingsRow>
      </SettingsSection>
      {notifications.error && (
        <p role="alert" className={styles.message}>
          {notifications.error}
        </p>
      )}
      {notifications.testStatus && (
        <p role="status" className={styles.message}>
          {notifications.testStatus}
        </p>
      )}
      <details className={styles.details}>
        <summary>Delivery help</summary>
        <p>
          Keep the server running and connected to your browser's push service.
        </p>
        <ul>
          <li>
            iPhone / iPad: in Safari, choose Share → Add to Home Screen. Enable
            notifications in the installed app.
          </li>
          <li>
            Android: allow notifications for this site and Chrome. Battery
            limits, force-stop and Do Not Disturb may block alerts.
          </li>
          <li>
            Delivery stays active for six hours after a visible visit. Missed
            alerts are not replayed.
          </li>
        </ul>
        <p>
          In-app notices still work without permission. System alerts may also
          appear for the chat you are viewing.
        </p>
      </details>
    </div>
  );
}
