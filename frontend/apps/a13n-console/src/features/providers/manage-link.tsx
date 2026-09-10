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
    <a
      className="inline-flex text-sm text-primary underline underline-offset-4"
      href={providersPath(category, scope, workspace?.key)}
      target="_blank"
      rel="noopener noreferrer"
    >
      {t("Manage providers")}
    </a>
  );
}
