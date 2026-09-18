import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useAccess } from "../../layout/workspace";
import { providersPath } from "./navigation";

/** Providers live inside the console, so this never opens a new tab. */
export function ManageProvidersLink({
  category,
  scope,
  variant = "outline",
}: {
  category: string;
  scope: "workspace" | "organization";
  variant?: "outline" | "ghost";
}) {
  const { t } = useTranslation();
  const { workspace } = useAccess();
  return (
    <Button
      variant={variant}
      size={variant === "ghost" ? "sm" : undefined}
      render={<Link to={providersPath(category, scope, workspace?.key)} />}
    >
      {t("Manage providers")}
    </Button>
  );
}
