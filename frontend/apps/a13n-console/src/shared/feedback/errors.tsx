import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
  Button,
  useToast,
} from "a13n-ui";
import { WarningCircleIcon } from "@phosphor-icons/react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { ApiError } from "../../service-client";
import styles from "./feedback.module.css";

/**
 * A refusal whose Service message carries identifiers or numbers reads from
 * its stable `details.reason` instead.
 */
function reasonCopy(error: ApiError, t: TFunction) {
  if (error.code !== "conflict") return undefined;
  const { reason, limit } = error.details;
  switch (reason) {
    case "mount_limit":
      return t(
        "A thread can mount at most {{limit}} environments. Remove one before adding another.",
        { limit },
      );
    case "memory_mount_limit":
      return t(
        "A thread can mount at most {{limit}} memories. Remove one before adding another.",
        { limit },
      );
    case "already_mounted":
      return t("The thread already mounts it under another name.");
    case "memory_full":
      return t(
        "This memory is full: its files may hold at most {{limit}} bytes. Shorten or delete files, then try again.",
        { limit },
      );
    case "environment_limit":
      return t(
        "This workspace has reached its limit of {{limit}} managed environments. Delete one it no longer needs, then try again.",
        { limit },
      );
    case "idempotency_key_reused":
      return t(
        "This request was already sent with different content. Send it again as a new request.",
      );
  }
  return undefined;
}

function errorDetails(error: unknown, t: TFunction) {
  const conflict = error instanceof ApiError && error.status === 412;
  return {
    title: t(conflict ? "This resource changed" : "Something went wrong"),
    description: conflict
      ? t(
          "Your draft is preserved. Reload the latest version before trying again.",
        )
      : error instanceof Error
        ? (error instanceof ApiError && reasonCopy(error, t)) ||
          t(error.message)
        : t("The request could not be completed."),
    requestId:
      error instanceof ApiError && error.requestId
        ? error.requestId
        : undefined,
  };
}

/** Isolated action results surface as a toast at the top of the viewport. */
export function ErrorToast({ error }: { error: unknown }) {
  return error ? <ErrorToastContent error={error} /> : null;
}

function ErrorToastContent({ error }: { error: unknown }) {
  const { t } = useTranslation();
  const toast = useToast();
  const toastId = useId();
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const details = errorDetails(error, t);
  useEffect(() => {
    toastRef.current.add({
      id: toastId,
      type: "error",
      priority: "high",
      timeout: 0,
      title: details.title,
      description: (
        <>
          {details.description}
          {details.requestId && (
            <small>
              {t("Request ID")}: {details.requestId}
            </small>
          )}
        </>
      ),
    });
  }, [details.description, details.requestId, details.title, toastId]);
  useEffect(
    () => () => {
      toastRef.current.close(toastId);
    },
    [toastId],
  );
  return null;
}

/** Reserved for views that cannot function at all. */
export function ErrorPage({
  error,
  title,
  actions,
}: {
  error: unknown;
  title?: string;
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  if (!error) return null;
  const details = errorDetails(error, t);
  return (
    <div className={styles.errorPage} role="alert">
      <div className={styles.errorMark}>
        <WarningCircleIcon aria-hidden="true" />
      </div>
      <h1>{title ?? details.title}</h1>
      <p>{details.description}</p>
      {details.requestId && (
        <small>
          {t("Request ID")}: {details.requestId}
        </small>
      )}
      {actions && <div className={styles.errorPageActions}>{actions}</div>}
    </div>
  );
}

/** Stays beside the form or collection that owns the failure. */
export function ErrorNotice({
  error,
  retry,
  description,
}: {
  error: unknown;
  retry?: () => void;
  /** Names the recovery when the owner already performed it, such as an autosaved row that reloaded. */
  description?: ReactNode;
}) {
  const { t } = useTranslation();
  if (!error) return null;
  const details = errorDetails(error, t);
  return (
    <Alert variant="error" className="my-4">
      <WarningCircleIcon aria-hidden="true" />
      <AlertTitle>{details.title}</AlertTitle>
      <AlertDescription>
        <p>{description ?? details.description}</p>
        {details.requestId && (
          <small>
            {t("Request ID")}: {details.requestId}
          </small>
        )}
      </AlertDescription>
      {retry && (
        <AlertAction>
          <Button size="sm" variant="outline" onClick={retry} type="button">
            {t("Reload")}
          </Button>
        </AlertAction>
      )}
    </Alert>
  );
}
