import { CopyIcon } from "@phosphor-icons/react";
import { useMutation } from "@tanstack/react-query";
import { Button, FormField, Input, MenuItem, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../feedback";
import shared from "../shared.module.css";

export function DuplicateAction({
  sourceName,
  title,
  description,
  nameLimit = 128,
  requiresKey = false,
  action,
}: {
  sourceName: string;
  title: string;
  description?: string;
  nameLimit?: number;
  requiresKey?: boolean;
  action: (name: string, key: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const mutation = useMutation({
    mutationFn: () => action(name.trim(), key),
    onSuccess: () => setOpen(false),
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={(next) => {
        if (next && !mutation.isPending) {
          const suffix = ` (${t("copy")})`;
          setName(
            Array.from(sourceName)
              .slice(0, nameLimit - suffix.length)
              .join("")
              .trimEnd() + suffix,
          );
          setKey("");
          mutation.reset();
        }
        setOpen(next);
      }}
      trigger={
        <MenuItem closeOnClick={false}>
          <CopyIcon aria-hidden="true" />
          {t("Duplicate")}
        </MenuItem>
      }
      title={title}
      description={description}
      closeLabel={t("Close")}
      size="md"
    >
      <form
        className={shared.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim() && !mutation.isPending) mutation.mutate();
        }}
      >
        <FormField label={t("Display name")}>
          <Input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        {requiresKey && (
          <FormField
            label={t("Model key")}
            description={t(
              "Choose a new key that is unique in this workspace.",
            )}
          >
            <Input
              required
              pattern="[a-z0-9][a-z0-9.\-]{0,127}"
              value={key}
              onChange={(event) => setKey(event.target.value)}
            />
          </FormField>
        )}
        <ErrorNotice error={mutation.error} />
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => setOpen(false)}
          >
            {t("Cancel")}
          </Button>
          <Button
            type="submit"
            loading={mutation.isPending}
            disabled={
              !name.trim() ||
              Array.from(name.trim()).length > nameLimit ||
              (requiresKey && !key)
            }
          >
            {t("Duplicate")}
          </Button>
        </div>
      </form>
    </ModalFrame>
  );
}
