import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Timestamp } from "./feedback";
import styles from "./shared.module.css";
/** Setup URLs are ephemeral bearer-like values: keep them in memory and validate navigation schemes. */
export function AuthorizationLink({
  url,
  expiresAt,
  sameTab = false,
}: {
  url?: string | null;
  expiresAt: string;
  sameTab?: boolean;
}) {
  const { t } = useTranslation();
  let href: string | undefined;
  if (url) {
    try {
      const parsed = new URL(url);
      if (
        ["https:", "http:"].includes(parsed.protocol) &&
        !parsed.username &&
        !parsed.password
      )
        href = parsed.href;
    } catch {
      /* Invalid external navigation remains unavailable. */
    }
  }
  return (
    <div className={styles.stack} role="status">
      {href ? (
        <a
          className={styles.back}
          href={href}
          target={sameTab ? "_self" : "_blank"}
          rel="noopener noreferrer"
        >
          {t("Continue authorization")} <ArrowSquareOutIcon size={14} />
        </a>
      ) : (
        <p>
          {t(
            "Authorization is pending. Refresh the connection after completing the provider flow.",
          )}
        </p>
      )}
      <small>
        {t("Expires")}: <Timestamp value={expiresAt} />
      </small>
    </div>
  );
}
