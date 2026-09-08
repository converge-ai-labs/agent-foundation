import type { ReactNode } from "react";
import { AlertCircle, ArrowLeft, Inbox, RefreshCw } from "lucide-react";
import { ApiError } from "@converge.ai/a13n";
import { Button, EmptyState, Spinner, Badge } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import styles from "./shared.module.css";

export function Loading() {
  const { t } = useTranslation();
  return (
    <div role="status" className={styles.loading}>
      <Spinner />
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
    <div role="alert" className={styles.error}>
      <AlertCircle size={17} />
      <div>
        <strong>
          {t(conflict ? "This resource changed" : "Something went wrong")}
        </strong>
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
          <Button size="sm" icon={<RefreshCw size={14} />} onClick={retry}>
            {t("Reload")}
          </Button>
        )}
      </div>
    </div>
  );
}
export function Page({
  title,
  description,
  actions,
  back,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  back?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.page}>
      {back && (
        <Link className={styles.back} to={back}>
          <ArrowLeft size={14} />
          {t("Back")}
        </Link>
      )}
      <header className={styles.pageHeader}>
        <div>
          <h1>{title}</h1>
          {description && <p>{description}</p>}
        </div>
        <div className={styles.actions}>{actions}</div>
      </header>
      {children}
    </div>
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
    <EmptyState
      icon={<Inbox size={24} />}
      title={title}
      description={description}
      action={action}
    />
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
    : ["failed", "error", "disabled", "canceled", "cancelled"].includes(state)
      ? "danger"
      : ["waiting", "queued", "pending"].includes(state)
        ? "warning"
        : "neutral";
  return (
    <Badge tone={tone}>
      {t(`state.${state}`, { defaultValue: state.replaceAll("_", " ") })}
    </Badge>
  );
}
export function Timestamp({ value }: { value?: string | null }) {
  const { i18n, t } = useTranslation();
  return (
    <time dateTime={value ?? undefined}>
      {value
        ? new Intl.DateTimeFormat(i18n.resolvedLanguage, {
            dateStyle: "medium",
            timeStyle: "short",
          }).format(new Date(value))
        : t("Unavailable")}
    </time>
  );
}
