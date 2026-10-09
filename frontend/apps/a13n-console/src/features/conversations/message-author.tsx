import { Button, Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { RobotIcon, UserIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { UserAvatar } from "../../layout/avatar";
import { CopyButton, Identifier } from "../../shared/identity";
import { useMessageAuthor } from "./message-authors";
import styles from "./message-author.module.css";

/** Attribution belongs to the source message, never to the Run carrying it. */
export function MessageAuthor({
  entryId,
  principalId,
  submittedAt,
}: {
  entryId?: string | null;
  principalId?: string | null;
  submittedAt?: string;
}) {
  const { t, i18n } = useTranslation();
  const { author, me, sameName, failed, retry } = useMessageAuthor(entryId);
  const principal = author?.principal;
  const id = author?.principal_id ?? principalId;
  const name = principal?.name ?? t("User");
  const service = principal?.kind === "service_account";
  const date = author?.submitted_at ?? submittedAt;
  const shortId = id ? id.slice(-8) : undefined;
  return (
    <div className={styles.line}>
      <Popover>
        <PopoverTrigger
          type="button"
          className={styles.trigger}
          aria-label={t("Show author: {{name}}", { name })}
        >
          {service ? (
            <RobotIcon className={styles.robot} aria-hidden="true" />
          ) : principal ? (
            <UserAvatar
              name={name}
              id={id ?? undefined}
              url={principal.image_url}
              className={styles.avatar}
            />
          ) : (
            <UserIcon className={styles.robot} aria-hidden="true" />
          )}
          <span className={styles.name}>{name}</span>
          {!principal && shortId && (
            <span className={styles.detail}>· {shortId}</span>
          )}
          {me && <span className={styles.detail}>· {t("You")}</span>}
          {service && (
            <span className={styles.detail}>· {t("Service account")}</span>
          )}
          {sameName && (
            <span className={styles.email}>
              ({principal?.email ?? shortId})
            </span>
          )}
        </PopoverTrigger>
        <PopoverPopup className={styles.card} align="start" sideOffset={6}>
          <strong>{name}</strong>
          <span className={styles.detail}>
            {principal
              ? t(service ? "Service account" : "User account")
              : t("Author details unavailable")}
          </span>
          {principal?.status === "disabled" && (
            <span className={styles.detail}>{t("Disabled")}</span>
          )}
          {principal?.email && (
            <div className={styles.field}>
              <span>{principal.email}</span>
              <CopyButton
                value={principal.email}
                iconOnly
                copyLabel={t("Copy email")}
              />
            </div>
          )}
          {id && (
            <div className={styles.field}>
              <Identifier value={id} />
              <CopyButton
                value={id}
                iconOnly
                copyLabel={t("Copy principal ID")}
              />
            </div>
          )}
          {failed && (
            <Button size="sm" variant="outline" onClick={retry}>
              {t("Retry")}
            </Button>
          )}
        </PopoverPopup>
      </Popover>
      {date && (
        <time
          className={styles.time}
          dateTime={date}
          title={new Date(date).toLocaleString(i18n?.resolvedLanguage)}
        >
          {new Date(date).toLocaleTimeString(i18n?.resolvedLanguage, {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </time>
      )}
    </div>
  );
}
