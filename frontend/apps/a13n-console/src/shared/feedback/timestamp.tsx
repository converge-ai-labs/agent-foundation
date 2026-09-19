import { useTranslation } from "react-i18next";
import { relativeTime } from "../time";

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
