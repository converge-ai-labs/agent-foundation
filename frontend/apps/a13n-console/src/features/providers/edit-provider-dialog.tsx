import { ModalFrame } from "a13n-ui";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ResourceModalTitle,
  type useResourceEditorState,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";

/**
 * The shell every provider editor opens in: the provider's name as the title,
 * its identifiers behind the reference popover, and one loading and failure
 * treatment. The category supplies the form.
 */
export function EditProviderDialog({
  modalProps,
  open,
  name,
  id,
  readOnly = false,
  description,
  loading = false,
  error,
  onOpenChange,
  children,
}: {
  modalProps: ReturnType<typeof useResourceEditorState>["modalProps"];
  open: boolean;
  name?: string;
  id?: string;
  readOnly?: boolean;
  description?: string;
  loading?: boolean;
  error?: unknown;
  onOpenChange?: ComponentProps<typeof ModalFrame>["onOpenChange"];
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <ModalFrame
      {...modalProps}
      onOpenChange={onOpenChange ?? modalProps.onOpenChange}
      size="lg"
      closeLabel={t("Close")}
      title={
        name && id ? (
          <ResourceModalTitle name={name} id={id} />
        ) : (
          t(readOnly ? "Provider" : "Edit provider")
        )
      }
      description={description}
    >
      {open &&
        (loading ? (
          <Loading variant="form" rows={4} />
        ) : error ? (
          <ErrorNotice error={error} />
        ) : (
          children
        ))}
    </ModalFrame>
  );
}
