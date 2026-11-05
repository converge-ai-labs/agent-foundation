import { useState } from "react";
import {
  Button,
  Input,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import { LinkSimple, Check, Copy } from "@phosphor-icons/react";
import { copyText } from "../platform";
import { useTranslation } from "../i18n";
import styles from "./native.module.css";

export function ViewLink({ getLink }: { getLink: () => string | undefined }) {
  const { t } = useTranslation();
  const [link, setLink] = useState("");
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <Popover
      onOpenChange={(open) => {
        if (open) {
          setLink(getLink() ?? "");
          setCopied(false);
          setFailed(false);
        }
      }}
    >
      <PopoverTrigger
        render={<Button variant="ghost" size="icon" />}
        aria-label={t("Share explorer view")}
        title={t("Copy view link")}
      >
        <LinkSimple />
      </PopoverTrigger>
      <PopoverPopup align="end" className={styles.viewLink}>
        <PopoverTitle>{t("View link")}</PopoverTitle>
        <p>
          {t(
            "Opens this view for people with access to this server. Your access key isn't included.",
          )}
        </p>
        <div className={styles.linkField}>
          <Input
            aria-label={t("View link")}
            value={link}
            readOnly
            onFocus={(event) => {
              event.currentTarget.select();
              event.currentTarget.scrollLeft = 0;
            }}
          />
          <Button
            variant="outline"
            aria-label={copied ? t("Copied") : t("Copy link")}
            disabled={!link}
            onClick={async () => {
              try {
                await copyText(link);
                setCopied(true);
                setFailed(false);
              } catch {
                setFailed(true);
              }
            }}
          >
            {copied ? <Check /> : <Copy />}
            {copied ? t("Copied") : t("Copy")}
          </Button>
        </div>
        {failed && (
          <p role="alert">
            {t("Couldn't copy. Select the link and copy it manually.")}
          </p>
        )}
      </PopoverPopup>
    </Popover>
  );
}
