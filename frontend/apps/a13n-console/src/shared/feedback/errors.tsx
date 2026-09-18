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
import { useTranslation } from "react-i18next";
import { ApiError } from "../../service-client";
import styles from "./feedback.module.css";

function errorDetails(error: unknown, t: (value: string) => string) {
  const conflict =
    error instanceof ApiError &&
    (error.status === 412 ||
      [
        "version_conflict",
        "thread_version_conflict",
        "queue_version_conflict",
      ].includes(error.code));
  return {
    conflict,
    title: t(conflict ? "This resource changed" : "Something went wrong"),
    description: conflict
      ? t(
          "Your draft is preserved. Reload the latest version before trying again.",
        )
      : error instanceof Error
        ? t(error.message)
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
}: {
  error: unknown;
  retry?: () => void;
}) {
  const { t } = useTranslation();
  if (!error) return null;
  const details = errorDetails(error, t);
  return (
    <Alert variant="error" className="my-4">
      <WarningCircleIcon aria-hidden="true" />
      <AlertTitle>{details.title}</AlertTitle>
      <AlertDescription>
        <p>{details.description}</p>
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
