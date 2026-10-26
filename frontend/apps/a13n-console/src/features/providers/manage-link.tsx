import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import { providersPath } from "./navigation";

/** Providers live inside the console, so this never opens a new tab. */
export function ManageProvidersLink({
  category,
  variant = "outline",
}: {
  category: string;
  variant?: "outline" | "ghost";
}) {
  const { t } = useTranslation();
  const { workspace } = useWorkspace();
  return (
    <Button
      variant={variant}
      size={variant === "ghost" ? "sm" : undefined}
      render={<Link to={providersPath(category, workspace)} />}
    >
      {t("Manage providers")}
    </Button>
  );
}
