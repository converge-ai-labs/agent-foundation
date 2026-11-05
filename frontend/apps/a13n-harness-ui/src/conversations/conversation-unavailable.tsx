import { useTranslation } from "../i18n";
import { errorMessage } from "../i18n/error-message";
import { Button } from "a13n-ui";
import { ChatCircleSlash } from "@phosphor-icons/react";
import { Link } from "react-router";
import { ApiError, isConnectionError } from "../transport/client";
import styles from "./conversation-unavailable.module.css";

export function isUnavailableChat(error: unknown) {
  return (
    error instanceof ApiError &&
    (error.code === "thread_missing" || [403, 404, 410].includes(error.status))
  );
}

// Detail owns whether this route can be opened. Do not render dependent
// history, live-replay and editor failures as separate explanations.
export function ConversationUnavailable({
  error,
  retry,
  retrying,
  hasDraft,
}: {
  error: unknown;
  retry: () => void;
  retrying: boolean;
  hasDraft: boolean;
}) {
  const { t } = useTranslation();
  const missing =
    error instanceof ApiError &&
    (error.code === "thread_missing" || [404, 410].includes(error.status));
  const denied = error instanceof ApiError && error.status === 403;
  const message = missing
    ? t("This chat may have been removed, or the link is out of date.")
    : denied
      ? t("You don't have access to this chat.")
      : isConnectionError(error)
        ? t("Check your connection and try again.")
        : error instanceof Error
          ? errorMessage(error, t)
          : t("Try opening it again.");
  return (
    <section className={styles.unavailable} aria-label={t("Chat unavailable")}>
      <div role="alert">
        <ChatCircleSlash size={24} aria-hidden="true" />
        <h2>
          {missing
            ? t("Chat not found")
            : denied
              ? t("Chat unavailable")
              : t("Couldn't open this chat")}
        </h2>
        <p>{message}</p>
        {hasDraft && <p>{t("Your unsent input is still in this tab.")}</p>}
      </div>
      <div className={styles.actions}>
        <Button variant="outline" loading={retrying} onClick={retry}>
          {t("Retry")}
        </Button>
        <Button variant="ghost" render={<Link to="/" />}>
          {t("New chat")}
        </Button>
      </div>
    </section>
  );
}
