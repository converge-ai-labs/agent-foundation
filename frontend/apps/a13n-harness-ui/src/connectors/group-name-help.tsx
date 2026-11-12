import { Info } from "@phosphor-icons/react";
import {
  Button,
  Popover,
  PopoverDescription,
  PopoverPopup,
  PopoverTrigger,
} from "a13n-ui";
import { useTranslation } from "../i18n";

export function GroupNameHelp() {
  const { t } = useTranslation();
  return (
    <Popover>
      <PopoverTrigger
        aria-label={t("About group names")}
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            tooltip={false}
            className="size-6 shrink-0 text-muted-foreground"
          />
        }
      >
        <Info size={14} />
      </PopoverTrigger>
      <PopoverPopup
        side="top"
        align="start"
        className="max-w-[min(280px,var(--available-width))]"
      >
        <PopoverDescription className="text-xs leading-5">
          {t("Unique tool group used by the model. You can change it later.")}
        </PopoverDescription>
      </PopoverPopup>
    </Popover>
  );
}
