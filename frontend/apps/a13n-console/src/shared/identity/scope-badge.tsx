import { Badge } from "a13n-ui";
import { useTranslation } from "react-i18next";

export function ScopeBadge({ workspaceId }: { workspaceId?: string | null }) {
  const { t } = useTranslation();
  return (
    <Badge variant="secondary" className="font-normal text-muted-foreground">
      {t(workspaceId ? "Workspace" : "Organization")}
    </Badge>
  );
}
