import { useTranslation } from "../i18n";
import { useState, type ReactElement } from "react";
import { Button, DialogClose, ModalFrame } from "a13n-ui";

/** Local confirmation for an explicit action; dismissing never runs the action. */
export function ConfirmAction({
  trigger,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive = false,
  confirmationRequired = true,
}: {
  trigger: ReactElement;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
  destructive?: boolean;
  confirmationRequired?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      bottomStickOnMobile
      trigger={trigger}
      open={open}
      onOpenChange={(nextOpen, details) => {
        if (nextOpen && !confirmationRequired) {
          details.cancel();
          onConfirm();
          return;
        }
        setOpen(nextOpen);
      }}
      title={title}
      description={description}
      closeLabel={t("Close confirmation")}
      footer={
        <>
          <DialogClose render={<Button variant="outline" />}>
            {t("Cancel")}
          </DialogClose>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={() => {
              setOpen(false);
              onConfirm();
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}
