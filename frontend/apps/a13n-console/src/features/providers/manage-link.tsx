import { ArrowSquareOutIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useAccess } from "../../layout/workspace";
import { providersPath } from "./navigation";
export function ManageProvidersLink({
  category,
  scope,
}: {
  category: string;
  scope: "workspace" | "organization";
}) {
  const { t } = useTranslation();
  const { workspace } = useAccess();
  return (
    <Button
      variant="ghost"
      className="text-muted-foreground"
      render={
        <a
          href={providersPath(category, scope, workspace?.key)}
          target="_blank"
          rel="noopener noreferrer"
        />
      }
    >
      {t("Manage providers")}
      <ArrowSquareOutIcon size={13} aria-hidden="true" />
    </Button>
  );
}
