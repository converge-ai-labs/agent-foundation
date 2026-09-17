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
  notificationWorker,
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
  if (!window.isSecureContext) return "unavailable";
  const appleMobile =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true;
  // Prefer capability detection; the platform hint only explains missing APIs.
  if (appleMobile && !standalone && !supportsPush()) return "install-required";
  return typeof Notification !== "undefined"
    ? Notification.permission
    : "unavailable";
}

// Serialize native delivery in same-origin tabs where Web Locks is available.
// The native tag also replaces duplicates on browsers without that API/storage.
export async function deliverOnce(
  id: string,
  deliver: () => boolean | Promise<boolean>,
) {
  const claim = async () => {
    if (readIds(DELIVERED).includes(id)) return;
    if (await deliver()) remember(DELIVERED, id);
  };
  if (navigator.locks)
    await navigator.locks.request("a13n-harness-ui.notifications", claim);
  else await claim();
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
  const match = useMatch("/threads/:threadId");
  const settings = useMatch("/settings/notifications");
  const queries = useQueryClient();
  const toast = useToast();
  const seen = useRef(new Set<string>());
  const visited = useRef(new Set<string>());
  const native = useRef(new Set<Notification>());
  const alive = useRef(false);
  const threadId = match?.params.threadId;
  const current = useRef({ enabled, navigate, toast, threadId, background });
  current.current = { enabled, navigate, toast, threadId, background };
  const closeNative = useCallback(() => {
    native.current.forEach((item) => item.close());
    native.current.clear();
  }, []);
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
        ? enablePush(transport, readIds(VISITED), false)
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
            "Background notifications could not be synchronized. Check your connection and reconnect them. Live alerts remain available while WebUI is open.",
          );
        }
      });
    return () => {
      active = false;
    };
  }, [transport, enabled, permission, threadId, refreshVersion, requesting]);
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
      ["denied", "unavailable", "install-required"].includes(
        notificationPermission(),
      )
    )
      return;
    setEnabled(true);
    setRequesting(true);
    try {
      if (supportsPush() && transport) {
        await enablePush(transport, readIds(VISITED));
        if (alive.current)
          setBackground(pushSubscriptionId() ? "enabled" : "disabled");
      } else {
        await Notification.requestPermission();
      }
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
  const showNative = useCallback(
    async (title: string, body: string, tag: string, path?: string) => {
      const allowed = () =>
        !(
          !alive.current ||
          !current.current.enabled ||
          readPreference(ENABLED, "true") === "false" ||
          notificationPermission() !== "granted" ||
          // Worker notifications share receipt tags with pushes, so a stale or
          // unreachable push service must not suppress a live mobile alert.
          (current.current.background === "enabled" &&
            !!pushSubscriptionId() &&
            !("serviceWorker" in navigator)) ||
          (tag !== "a13n-harness-ui.test" &&
            document.visibilityState === "visible" &&
            document.hasFocus())
        );
      if (!allowed()) return false;
      const retain = (notification: Notification) => {
        native.current.add(notification);
        if (native.current.size > 64) {
          const oldest = native.current.values().next().value!;
          oldest.close();
          native.current.delete(oldest);
        }
      };
      try {
        const options = {
          body: Array.from(body).slice(0, 180).join(""),
          // Explicit tests are new attempts, not replayed operation receipts.
          tag:
            tag === "a13n-harness-ui.test" && "serviceWorker" in navigator
              ? `${tag}.${crypto.randomUUID()}`
              : tag,
        };
        if ("serviceWorker" in navigator) {
          const registration = await notificationWorker();
          // Focus, permission and opt-out may change while the worker starts.
          if (!allowed()) return false;
          if (
            (await registration.getNotifications({ tag: options.tag })).length
          )
            return true;
          if (!allowed()) return false;
          await registration.showNotification(title, {
            ...options,
            icon: "/icons/icon-192.png",
            data: { path: path ?? "/settings/notifications" },
          });
          for (const notification of await registration.getNotifications({
            tag,
          })) {
            if (!allowed()) notification.close();
            else retain(notification);
          }
          if (alive.current && tag === "a13n-harness-ui.test")
            setTestStatus(
              "The browser accepted the test notification. This does not confirm a system banner; check your device's notification settings.",
            );
          return true;
        }
        const notification = new Notification(title, options);
        retain(notification);
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
        if (tag === "a13n-harness-ui.test")
          setTestStatus(
            "Test requested from the browser. This does not confirm that your device displayed a banner.",
          );
        return true;
      } catch {
        if (alive.current)
          setError(
            "This browser could not display a notification. Check site permission and system notification settings. In-app notices remain available.",
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
        return showNative(
          title,
          notice.brief,
          `a13n-harness-ui.${notice.receipt_id}`,
          path,
        );
      }).catch(() => {
        // Optional cross-tab coordination must not silently swallow an alert.
        // The native tag still provides best-effort replacement.
        showNative(
          title,
          notice.brief,
          `a13n-harness-ui.${notice.receipt_id}`,
          path,
        );
      });
    },
    [queries, showNative],
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
    void showNative(
      "Harness UI test notification",
      "Task results and requests for your input will appear here while WebUI is open.",
      "a13n-harness-ui.test",
    );
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
            "Browser permission is already allowed. Background delivery is a separate subscription for alerts while WebUI is closed."
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
              Task results and requests for your input, for conversations opened
              on this device. Previews may appear on your lock screen.
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
            ? "The subscription was synchronized, not delivery-confirmed. You can close WebUI or lock your phone. The server must stay running and able to reach your browser's push service."
            : notifications.permission !== "granted"
              ? "Allow notifications in a supported browser or Home Screen app to receive system alerts. In-app notices remain available."
              : "Without background delivery, keep WebUI open to receive live alerts."}
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
              notifications.background === "checking"
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
          even while WebUI is open. Missed history is not replayed as new
          alerts.
        </p>
      </Panel>
    </>
  );
}
