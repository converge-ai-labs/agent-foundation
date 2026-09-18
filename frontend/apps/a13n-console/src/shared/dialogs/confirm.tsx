import { Button, type ButtonProps, ModalFrame } from "a13n-ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../feedback";
import styles from "./dialogs.module.css";

/** Destructive confirmations name the resource and label the action. */
export function Confirm({
  title,
  description,
  subject,
  action,
  onSuccess,
  trigger,
  triggerElement,
  danger = false,
  triggerVariant,
  retry,
  children,
}: {
  title: string;
  description: string;
  subject: string;
  action: () => Promise<unknown>;
  onSuccess?: () => void;
  trigger?: ReactNode;
  triggerElement?: ReactElement;
  danger?: boolean;
  triggerVariant?: ButtonProps["variant"];
  retry?: () => void;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const mutation = useMutation({
    mutationFn: action,
    onSuccess: () => {
      if (onSuccess) onSuccess();
      else void cache.invalidateQueries();
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        triggerElement ?? (
          <Button
            variant={triggerVariant ?? (danger ? "destructive" : "outline")}
            className={
              triggerVariant === "ghost"
                ? danger
                  ? "font-normal text-destructive-foreground"
                  : "font-normal text-muted-foreground"
                : undefined
            }
            size="sm"
            type="button"
          >
            {trigger}
          </Button>
        )
      }
      size="md"
      title={title}
      description={description}
      closeLabel={t("Close")}
      footer={
        <>
          <Button
            variant="outline"
            disabled={mutation.isPending}
            onClick={() => setOpen(false)}
            type="button"
          >
            {t("Cancel")}
          </Button>
          <Button
            variant={danger ? "destructive" : "default"}
            loading={mutation.isPending}
            onClick={() => mutation.mutate()}
            type="button"
          >
            {title}
          </Button>
        </>
      }
      open={open}
    >
      <p className={styles.confirmSubject}>{subject}</p>
      {(children || mutation.error) && (
        <>
          {children}
          <ErrorNotice error={mutation.error} retry={retry} />
        </>
      )}
    </ModalFrame>
  );
}
