import { Button } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function CredentialEditor({
  configured,
  removing = false,
  onRemovingChange,
  children,
}: {
  configured?: boolean;
  removing?: boolean;
  onRemovingChange?: (value: boolean) => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className="grid gap-3" aria-label={t("Credentials")}>
      {configured !== undefined && (
        <div className="flex items-center justify-between gap-4 text-xs text-muted-foreground">
          <span>
            {t(
              removing
                ? "Credentials will be removed when you save."
                : configured
                  ? "Credentials saved"
                  : "No credentials configured",
            )}
          </span>
          {configured && onRemovingChange && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onRemovingChange(!removing)}
            >
              {t(removing ? "Undo removal" : "Remove")}
            </Button>
          )}
        </div>
      )}
      {!removing && configured && (
        <p className="text-xs text-muted-foreground">
          {t("Leave blank to keep saved credentials.")}
        </p>
      )}
      {!removing && children}
    </section>
  );
}
