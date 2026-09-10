import { Button } from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";

export function ResourceEditorButton({
  editing = false,
  createLabel,
  editLabel = "Edit",
  ...props
}: Omit<ComponentProps<typeof Button>, "children" | "size" | "variant"> & {
  editing?: boolean;
  createLabel: string;
  editLabel?: string;
}) {
  const { t } = useTranslation();
  return (
    <Button
      {...props}
      type="button"
      size={editing ? "sm" : "default"}
      variant={editing ? "outline" : "default"}
    >
      {!editing && <PlusIcon aria-hidden="true" />}
      {t(editing ? editLabel : createLabel)}
    </Button>
  );
}
