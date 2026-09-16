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
import { useQueryClient } from "@tanstack/react-query";
import { Bell } from "@phosphor-icons/react";
import { Button, Switch, ToastProvider, useToast } from "a13n-ui";
import type { Schema } from "../transport/client";
import { readPreference, writePreference } from "./preferences";
import { PageHeader, Panel } from "./ui";
import styles from "./notifications.module.css";

type Permission = NotificationPermission | "unavailable";
type SummaryEvent = Schema<"SummaryInvalidation">;
const ENABLED = "notifications.enabled";
const VISITED = "notifications.conversations";
const DELIVERED = "notifications.delivered";

function readIds(key: string): string[] {
  try {
    const value: unknown = JSON.parse(readPreference(key, "[]"));
    return Array.isArray(value)
      ? value.filter((id): id is string => typeof id === "string").slice(-256)
      : [];
  } catch {
    return [];
  }
}
function remember(key: string, id: string) {
  writePreference(
    key,
    JSON.stringify(
      [...readIds(key).filter((item) => item !== id), id].slice(-256),
    ),
  );
}
export function notificationPermission(): Permission {
  return window.isSecureContext && typeof Notification !== "undefined"
    ? Notification.permission
    : "unavailable";
}

// Serialize native delivery in same-origin tabs where Web Locks is available.
// The native tag also replaces duplicates on browsers without that API/storage.
export async function deliverOnce(id: string, deliver: () => boolean) {
  const claim = () => {
    if (readIds(DELIVERED).includes(id)) return;
    if (deliver()) remember(DELIVERED, id);
  };
  if (navigator.locks)
    await navigator.locks.request("a13n-harness-ui.notifications", claim);
  else claim();
}

type Notifications = {
  enabled: boolean;
  permission: Permission;
  requesting: boolean;
  error: string;
  testStatus: string;
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
  const [enabled, updateEnabled] = useState(
    () => readPreference(ENABLED, "true") !== "false",
  );
  const [permission, setPermission] = useState(notificationPermission);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState("");
  const [testStatus, setTestStatus] = useState("");
  const navigate = useNavigate();
  const match = useMatch("/threads/:threadId");
  const queries = useQueryClient();
  const toast = useToast();
  const seen = useRef(new Set<string>());
  const visited = useRef(new Set<string>());
  const native = useRef(new Set<Notification>());
  const alive = useRef(false);
  const threadId = match?.params.threadId;
  const current = useRef({ enabled, navigate, toast, threadId });
  current.current = { enabled, navigate, toast, threadId };
  const closeNative = useCallback(() => {
    native.current.forEach((item) => item.close());
    native.current.clear();
  }, []);
  useEffect(() => {
    alive.current = true;
    const refresh = () => {
      setPermission(notificationPermission());
      updateEnabled(
        readPreference(ENABLED, String(current.current.enabled)) !== "false",
      );
    };
    window.addEventListener("focus", refresh);
    window.addEventListener("storage", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      alive.current = false;
      window.removeEventListener("focus", refresh);
      window.removeEventListener("storage", refresh);
      document.removeEventListener("visibilitychange", refresh);
      closeNative();
    };
  }, [closeNative]);
  useEffect(() => {
    if (match?.params.threadId) {
      visited.current.add(match.params.threadId);
      remember(VISITED, match.params.threadId);
    }
  }, [match?.params.threadId]);
  useEffect(() => {
    if (!enabled || permission !== "granted") closeNative();
  }, [enabled, permission, closeNative]);
  const setEnabled = (value: boolean) => {
    current.current.enabled = value;
    writePreference(ENABLED, String(value));
    updateEnabled(value);
    setPermission(notificationPermission());
    setError("");
    setTestStatus("");
  };
  const request = async () => {
    if (requesting || notificationPermission() !== "default") return;
    setEnabled(true);
    setRequesting(true);
    try {
      const next = await Notification.requestPermission();
      if (alive.current) setPermission(next);
    } catch {
      if (alive.current)
        setError(
          "Notification permission could not be requested. Check this site's browser settings.",
        );
    } finally {
      if (alive.current) setRequesting(false);
    }
  };
  const showNative = useCallback(
    (title: string, body: string, tag: string, path?: string) => {
      if (
        !alive.current ||
        !current.current.enabled ||
        readPreference(ENABLED, "true") === "false" ||
        notificationPermission() !== "granted" ||
        (tag !== "a13n-harness-ui.test" &&
          document.visibilityState === "visible" &&
          document.hasFocus())
      )
        return false;
      try {
        const notification = new Notification(title, {
          body: Array.from(body).slice(0, 180).join(""),
          tag,
        });
        native.current.add(notification);
        if (native.current.size > 64) {
          const oldest = native.current.values().next().value!;
          oldest.close();
          native.current.delete(oldest);
        }
        notification.onshow = () => {
          if (alive.current && tag === "a13n-harness-ui.test")
            setTestStatus(
              "The browser reported the test notification as shown. If no banner appeared, check your system notification settings.",
            );
        };
        notification.onerror = () => {
          native.current.delete(notification);
          if (!alive.current) return;
          setTestStatus("");
          setError(
            "This browser could not display a desktop notification. Check site permission and system notification settings. In-app notices remain available.",
          );
        };
        notification.onclose = () => native.current.delete(notification);
        notification.onclick = () => {
          if (alive.current) {
            window.focus();
            if (path) current.current.navigate(path);
          }
          notification.close();
          native.current.delete(notification);
        };
        return true;
      } catch {
        setError(
          "This browser could not display a desktop notification. In-app notices remain available.",
        );
        return false;
      }
    },
    [],
  );
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
      // Browser-local interest follows conversations opened here, not every
      // collaborator's work. Opening history does not synthesize notices.
      if (
        !visited.current.has(threadId) &&
        !readIds(VISITED).includes(threadId)
      )
        return;
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
      if (
        notice.status !== "completed" ||
        current.current.threadId !== threadId
      )
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
      if (!current.current.enabled || notificationPermission() !== "granted")
        return;
      void deliverOnce(id, () => {
        return showNative(title, notice.brief, id, path);
      }).catch(() => {
        // Optional cross-tab coordination must not silently swallow an alert.
        // The native tag still provides best-effort replacement.
        showNative(title, notice.brief, id, path);
      });
    },
    [queries, showNative],
  );
  const test = () => {
    setError("");
    setTestStatus("");
    const requested = showNative(
      "Harness UI test notification",
      "Task results and requests for your input will appear here while WebUI is open.",
      "a13n-harness-ui.test",
    );
    if (requested)
      setTestStatus(
        "Test requested from the browser. This does not confirm that macOS displayed a banner.",
      );
  };
  const value = {
    enabled,
    permission,
    requesting,
    error,
    testStatus,
    setEnabled,
    request,
    test,
    receive,
  };
  return (
    <NotificationsContext value={value}>
      <div className={styles.frame}>
        {children}
        {enabled && permission !== "granted" && <PermissionToast />}
      </div>
    </NotificationsContext>
  );
}

const permissionLabels: Record<Permission, string> = {
  default: "Not yet allowed",
  granted: "Allowed",
  denied: "Blocked by browser",
  unavailable: "Unavailable here",
};
function PermissionDescription({ permission }: { permission: Permission }) {
  return (
    <>
      {permission === "denied"
        ? "Notifications are blocked. Allow them in this site's browser settings to receive task alerts."
        : permission === "unavailable"
          ? "Desktop notifications require a supported browser on HTTPS or localhost. In-app notices remain available."
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
          {notifications.permission === "default"
            ? "Enable task notifications"
            : "Set up task notifications"}
        </h2>
        <p>
          <PermissionDescription permission={notifications.permission} />
        </p>
        {notifications.error && <p role="alert">{notifications.error}</p>}
        <div className={styles.actions}>
          {notifications.permission === "default" && (
            <Button
              size="sm"
              onClick={() => void notifications.request()}
              loading={notifications.requesting}
            >
              Enable notifications
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
      <Panel title="Desktop notifications">
        <div className={styles.settingRow}>
          <div>
            <label htmlFor="desktop-notifications">
              Enable browser notifications
            </label>
            <p>
              For conversations opened in this browser, while WebUI remains
              open. Notifications may show result text on your desktop or lock
              screen.
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
        <div className={styles.actions}>
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
              !notifications.enabled || notifications.permission !== "granted"
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
          Alerts are sent even while this page is in the foreground. On macOS,
          check System Settings → Notifications for your browser or this
          website, and check whether Focus is silencing alerts. Site permission
          alone does not guarantee a desktop banner.
        </p>
        <p>
          In-app task notices remain available without desktop permission.
          Closing WebUI stops live notifications; missed history is not replayed
          as new alerts.
        </p>
      </Panel>
    </>
  );
}
