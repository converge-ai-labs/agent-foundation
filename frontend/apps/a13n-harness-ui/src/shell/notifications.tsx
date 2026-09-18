import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Link, useMatch, useNavigate } from "react-router";
import { fitVisualViewport } from "./visual-viewport";
import { useQueryClient } from "@tanstack/react-query";
import { Bell } from "@phosphor-icons/react";
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
import { PageHeader, Panel } from "./ui";
import styles from "./notifications.module.css";

type Permission = NotificationPermission | "unavailable" | "install-required";
type Background = "disabled" | "checking" | "enabled" | "error";
type SummaryEvent = Schema<"SummaryInvalidation">;
const ENABLED = "notifications.enabled";
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
  const settings = useMatch("/settings/notifications");
  const queries = useQueryClient();
  const toast = useToast();
  const seen = useRef(new Set<string>());
  const alive = useRef(false);
  const current = useRef({ enabled, navigate, toast, background });
  current.current = { enabled, navigate, toast, background };
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
            "Background notifications could not be synchronized. Check your connection and reconnect them. In-app notices remain available while WebUI is open.",
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
      const detail = queries.getQueryData<Schema<"ThreadDetail">>([
        "thread",
        threadId,
        "detail",
      ]);
      const name =
        detail?.thread.title ||
        detail?.thread.excerpt?.first_input ||
        "Conversation";
      const state =
        notice.status === "completed"
          ? "Task completed"
          : notice.status === "failed"
            ? "Task failed"
            : "Your input is needed";
      const title = `${state} · ${Array.from(name).slice(0, 80).join("")}`;
      const path = `/threads/${encodeURIComponent(threadId)}`;
      current.current.toast.add({
        id,
        title,
        description: notice.brief,
        type:
          notice.status === "failed"
            ? "error"
            : notice.status === "suspended"
              ? "warning"
              : "success",
        timeout: notice.status === "suspended" ? 0 : 8000,
        actionProps: {
          children: "Open conversation",
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
              "The push service accepted the test. This does not confirm delivery to your device; check the notification and your system settings.",
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
          !settings &&
          !requesting &&
          background !== "checking" &&
          (permission !== "granted" ||
            (supportsPush() &&
              (background === "disabled" || background === "error"))) && (
            <PermissionToast />
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
        ? "On iPhone or iPad, notifications require iOS/iPadOS 16.4 or later and a Home Screen web app, not a regular Chrome or Safari tab. Use Share → Add to Home Screen, then open Harness UI from its Home Screen icon and enable notifications there. If Chrome does not offer this action, open this address in Safari to add it."
        : permission === "denied"
          ? "Notifications are blocked. Allow them in this site's browser settings (or the Home Screen app's system notification settings) to receive task alerts."
          : permission === "unavailable"
            ? "Notifications require a supported browser on HTTPS or localhost. On iPhone or iPad, update iOS/iPadOS and open Harness UI from its Home Screen icon. In-app notices remain available."
            : "Get notified when an agent finishes, fails, or needs your input, with a preview of the actual result."}
    </>
  );
}
function PermissionToast() {
  const notifications = useNotifications()!;
  return (
    <aside
      className={styles.permissionToast}
      aria-label="Notification permission"
    >
      <Bell size={20} aria-hidden="true" />
      <div>
        <h2>
          {notifications.permission === "granted"
            ? notifications.background === "error"
              ? "Reconnect background notifications"
              : "Enable background notifications"
            : notifications.permission === "default"
              ? "Enable task notifications"
              : "Set up task notifications"}
        </h2>
        <p>
          {notifications.permission === "granted" ? (
            "Browser permission is already allowed. A push subscription is also required for system alerts, whether WebUI is open or closed."
          ) : (
            <PermissionDescription permission={notifications.permission} />
          )}
        </p>
        {notifications.error && <p role="alert">{notifications.error}</p>}
        <div className={styles.actions}>
          {(notifications.permission === "default" ||
            (notifications.permission === "granted" && supportsPush())) && (
            <Button
              size="sm"
              onClick={() => void notifications.request()}
              loading={notifications.requesting}
            >
              {notifications.permission === "granted"
                ? notifications.background === "error"
                  ? "Reconnect"
                  : "Enable background delivery"
                : "Enable notifications"}
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => notifications.setEnabled(false)}
          >
            Turn off reminders
          </Button>
          <Link to="/settings/notifications">Settings</Link>
        </div>
      </div>
    </aside>
  );
}
export function NotificationSettings() {
  const notifications = useNotifications()!;
  return (
    <>
      <PageHeader
        title="Notifications"
        description="Personal preferences for this browser. Changes apply immediately and do not affect other collaborators."
      />
      <Panel title="Browser notifications">
        <div className={styles.settingRow}>
          <div>
            <label htmlFor="desktop-notifications">
              Enable browser notifications
            </label>
            <p>
              Task results and requests for your input across all conversations.
              Previews may appear on your lock screen.
            </p>
          </div>
          <Switch
            id="desktop-notifications"
            checked={notifications.enabled}
            onCheckedChange={notifications.setEnabled}
          />
        </div>
        <p>
          Browser permission:{" "}
          <strong>{permissionLabels[notifications.permission]}</strong>
        </p>
        {notifications.permission !== "granted" && (
          <p>
            <PermissionDescription permission={notifications.permission} />
          </p>
        )}
        <p>
          Background delivery:{" "}
          <strong>{backgroundLabels[notifications.background]}</strong>
          <br />
          {notifications.background === "enabled"
            ? "The subscription was synchronized, not delivery-confirmed. System alerts continue for six hours after this device last had a visible WebUI page. The server must stay running and able to reach your browser's push service."
            : notifications.permission !== "granted"
              ? "Allow notifications in a supported browser or Home Screen app to receive system alerts. In-app notices remain available."
              : "Without working push delivery, only in-app notices are available while WebUI is open."}
        </p>
        <div className={styles.actions}>
          {notifications.enabled &&
            notifications.permission === "granted" &&
            supportsPush() && (
              <Button
                onClick={() => void notifications.request()}
                loading={notifications.requesting}
                variant={
                  notifications.background === "enabled" ? "outline" : "default"
                }
              >
                {pushSubscriptionId()
                  ? "Reconnect background notifications"
                  : "Enable background notifications"}
              </Button>
            )}
          {notifications.enabled && notifications.permission === "default" && (
            <Button
              variant="outline"
              onClick={() => void notifications.request()}
              loading={notifications.requesting}
            >
              Allow notifications
            </Button>
          )}
          <Button
            variant="outline"
            disabled={
              !notifications.enabled ||
              notifications.permission !== "granted" ||
              notifications.requesting ||
              notifications.background !== "enabled"
            }
            onClick={notifications.test}
          >
            Send test notification
          </Button>
        </div>
        {notifications.error && <p role="alert">{notifications.error}</p>}
        {notifications.testStatus && (
          <p role="status">{notifications.testStatus}</p>
        )}
        <p>
          On Android Chrome, allow this site's notifications and Chrome's system
          notifications. Force-stopping the browser, battery restrictions, or Do
          Not Disturb can prevent alerts. On iPhone or iPad, enable
          notifications inside the Home Screen app, not a Chrome or Safari tab.
          Permission alone does not confirm delivery.
        </p>
        <p>
          In-app notices remain available without notification permission.
          Background push uses your browser's push service and can show alerts
          alongside in-app notices even for the conversation you are viewing.
          Keeping a WebUI page visible renews the six-hour delivery window.
          Missed history is not replayed as new alerts.
        </p>
      </Panel>
    </>
  );
}
