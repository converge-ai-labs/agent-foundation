import { Button, Input } from "a13n-ui";
import { Camera, Trash2 } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

export function ImagePicker({
  children,
  hasImage,
  editable,
  pending,
  onChange,
  description,
}: {
  children: ReactNode;
  description?: ReactNode;
  hasImage: boolean;
  editable: boolean;
  pending: boolean;
  onChange: (file: File | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-4">
      {editable ? (
        <Button
          type="button"
          variant="ghost"
          className="group relative h-auto w-auto sm:h-auto overflow-hidden rounded-xl p-0"
          disabled={pending}
          aria-label={t("Upload image")}
          onClick={() => input.current?.click()}
        >
          {children}
          <span
            className="absolute inset-0 flex items-center justify-center bg-black/40 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
            aria-hidden="true"
          >
            <Camera size={16} />
          </span>
        </Button>
      ) : (
        children
      )}
      {editable && (
        <>
          <Input
            ref={input}
            type="file"
            nativeInput
            unstyled
            className="hidden"
            accept="image/png,image/jpeg,image/webp"
            hidden
            disabled={pending}
            aria-label={t("Upload image")}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) onChange(file);
              event.target.value = "";
            }}
          />
          <div className="min-w-0 space-y-1.5">
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={() => input.current?.click()}
              >
                {t("Upload image")}
              </Button>
              {hasImage && (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  disabled={pending}
                  aria-label={t("Remove image")}
                  onClick={() => onChange(null)}
                >
                  <Trash2 size={14} />
                </Button>
              )}
            </div>
            {description && (
              <div className="text-xs leading-relaxed text-muted-foreground">
                {description}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
