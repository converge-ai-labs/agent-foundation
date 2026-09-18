import { ArrowUpRightIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";

export function ProviderKeyLink({
  href,
  label = "Get an API Key",
}: {
  href: string;
  label?: string;
}) {
  const { t } = useTranslation();
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex shrink-0 items-center gap-1 rounded-sm text-[12.5px] font-medium text-muted-foreground transition-colors hover:text-foreground hover:underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      {t(label)}
      <ArrowUpRightIcon aria-hidden className="size-3" />
    </a>
  );
}
