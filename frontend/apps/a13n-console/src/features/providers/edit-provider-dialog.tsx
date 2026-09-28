import { ModalFrame } from "a13n-ui";
import type { ComponentProps, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ResourceModalTitle,
  type useResourceEditorState,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { ProviderIcon } from "../../shared/identity";

/**
 * The shell every provider editor opens in: the service's mark and the
 * provider's name as the title, what it is underneath, and one
 * loading and failure treatment. The category supplies the form.
 */
export function EditProviderDialog({
  modalProps,
  open,
  name,
  id,
  type,
  definition,
  readOnly = false,
  loading = false,
  error,
  onOpenChange,
  children,
}: {
  modalProps: ReturnType<typeof useResourceEditorState>["modalProps"];
  open: boolean;
  name?: string;
  id?: string;
  /** The provider type, which carries the brand mark into the title. */
  type?: string;
  /** The definition's display name, which leads the description. */
  definition?: string;
  readOnly?: boolean;
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
          <ResourceModalTitle
            name={name}
            id={id}
            icon={type ? <ProviderIcon type={type} /> : undefined}
          />
        ) : (
          t(readOnly ? "Provider" : "Edit provider")
        )
      }
      description={definition}
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
