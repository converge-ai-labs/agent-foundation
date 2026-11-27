import { PuzzlePieceIcon } from "@phosphor-icons/react";
import { Badge } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useAppLabel } from "../../app-plugins/context";

export function AppBadge({
  appId,
  managed,
  onClick,
}: {
  appId: string;
  managed?: boolean;
  onClick?: () => void;
}) {
  const { t } = useTranslation();
  const name = useAppLabel(appId);
  return (
    <Badge
      variant="secondary"
      className="max-w-44 min-w-0 shrink justify-start gap-1 text-xs font-normal text-muted-foreground"
      title={name === appId ? appId : `${name} (${appId})`}
      render={
        onClick ? (
          <button
            type="button"
            onClick={onClick}
            aria-label={t("Filter by {{label}}", { label: name })}
          />
        ) : undefined
      }
    >
      <PuzzlePieceIcon aria-hidden="true" className="size-3 shrink-0" />
      <span className="truncate">
        {managed ? t("Managed by {{appId}}", { appId: name }) : name}
      </span>
    </Badge>
  );
}
