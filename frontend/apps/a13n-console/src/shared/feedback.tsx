import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  useToast,
} from "a13n-ui";

import {
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  Empty as EmptyRoot,
  EmptyTitle,
} from "a13n-ui";

import { ApiError } from "../service-client";
import {
  WarningCircleIcon,
  ArrowLeftIcon,
  TrayIcon,
} from "@phosphor-icons/react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { PageActionsTarget } from "./page-actions";

import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import styles from "./shared.module.css";
import { relativeTime } from "./time";

export { InlineLoading, Loading } from "./loading";

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
export function Page({
  title,
  titleAction,
  description,
  actions,
  back,
  className,
  children,
}: {
  title: string;
  titleAction?: ReactNode;
  description?: string;
  actions?: ReactNode;
  back?: string;
  className?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(
    null,
  );
  return (
    <PageActionsTarget value={actionsTarget}>
      <div className={`${styles.page} ${className ?? ""}`}>
        {back && (
          <Link className={styles.back} to={back}>
            <ArrowLeftIcon size={14} />
            {t("Back")}
          </Link>
        )}
        <header className={styles.pageHeader}>
          <div>
            <div className={styles.titleRow}>
              <h1>{title}</h1>
              {titleAction}
            </div>
            {description && <p>{description}</p>}
          </div>
          <div className={styles.actions} ref={setActionsTarget}>
            {actions}
          </div>
        </header>
        {children}
      </div>
    </PageActionsTarget>
  );
}
export function Empty({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <EmptyRoot>
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <TrayIcon aria-hidden="true" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
      {action && <EmptyContent>{action}</EmptyContent>}
    </EmptyRoot>
  );
}
export function StateBadge({
  state,
  label,
}: {
  state: string;
  label?: string;
}) {
  const { t } = useTranslation();
  const tone = [
    "completed",
    "succeeded",
    "active",
    "ready",
    "enabled",
  ].includes(state)
    ? "success"
    : ["failed", "error"].includes(state)
      ? "error"
      : ["waiting", "queued", "pending"].includes(state)
        ? "warning"
        : "secondary";
  return (
    <Badge variant={tone} data-state={state}>
      {label ??
        t(`state.${state}`, { defaultValue: state.replaceAll("_", " ") })}
    </Badge>
  );
}
export function Timestamp({
  value,
  relative = false,
}: {
  value?: string | null;
  relative?: boolean;
}) {
  const { i18n, t } = useTranslation();
  const date = value ? new Date(value) : undefined;
  const valid = date && Number.isFinite(date.getTime());
  const full = valid
    ? new Intl.DateTimeFormat(i18n.resolvedLanguage, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date)
    : t("Unavailable");
  return (
    <time dateTime={valid ? value! : undefined} title={full}>
      {valid && relative ? relativeTime(date, i18n.resolvedLanguage) : full}
    </time>
  );
}
