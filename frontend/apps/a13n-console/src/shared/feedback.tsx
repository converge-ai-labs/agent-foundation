import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  Spinner,
} from "a13n-ui";

import {
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  Empty as EmptyRoot,
  EmptyTitle,
} from "a13n-ui";

import { ApiError } from "@converge.ai/a13n";
import {
  WarningCircleIcon,
  ArrowLeftIcon,
  TrayIcon,
  ArrowsClockwiseIcon,
} from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { PageActionsTarget } from "./page-actions";

import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import styles from "./shared.module.css";
import { relativeTime } from "./time";

export function Loading() {
  const { t } = useTranslation();
  return (
    <div role="status" className={styles.loading}>
      <Spinner aria-hidden="true" />
      {t("Loading…")}
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
  const conflict =
    error instanceof ApiError &&
    (error.status === 412 ||
      [
        "version_conflict",
        "thread_version_conflict",
        "queue_version_conflict",
      ].includes(error.code));
  return (
    <Alert variant="error" className="my-4">
      <WarningCircleIcon aria-hidden="true" />
      <AlertTitle>
        {t(conflict ? "This resource changed" : "Something went wrong")}
      </AlertTitle>
      <AlertDescription>
        <p>
          {conflict
            ? t(
                "Your draft is preserved. Reload the latest version before trying again.",
              )
            : error instanceof Error
              ? t(error.message)
              : t("The request could not be completed.")}
        </p>
        {error instanceof ApiError && error.requestId && (
          <small>
            {t("Request ID")}: {error.requestId}
          </small>
        )}
        {retry && (
          <Button size="sm" variant="outline" onClick={retry} type="button">
            {<ArrowsClockwiseIcon size={14} />}
            {t("Reload")}
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
export function Page({
  title,
  description,
  actions,
  back,
  className,
  children,
}: {
  title: string;
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
            <h1>{title}</h1>
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
export function StateBadge({ state }: { state: string }) {
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
    <Badge variant={tone}>
      {t(`state.${state}`, { defaultValue: state.replaceAll("_", " ") })}
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
