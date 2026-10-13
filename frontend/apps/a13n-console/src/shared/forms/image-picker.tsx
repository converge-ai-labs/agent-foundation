import { Button, Input } from "a13n-ui";
import { CameraIcon, TrashIcon } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../feedback";
import { validateImageFile } from "./image";
import { ImageCropDialog } from "./image-crop";

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
  onChange: (file: File | null) => void | Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const finalFocus = useRef<HTMLElement | null>(null);
  const [url, setUrl] = useState<string>();
  const [error, setError] = useState<Error>();
  const { t } = useTranslation();
  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);
  return (
    <div>
      <div className="flex items-center gap-4">
        {editable ? (
          <Button
            type="button"
            variant="ghost"
            className="group relative h-auto w-auto sm:h-auto overflow-hidden rounded-xl p-0"
            disabled={pending}
            aria-label={t("Upload image")}
            onClick={(event) => {
              finalFocus.current = event.currentTarget;
              input.current?.click();
            }}
          >
            {children}
            <span
              className="absolute inset-0 flex items-center justify-center bg-black/40 text-white opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
              aria-hidden="true"
            >
              <CameraIcon size={16} />
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
                event.target.value = "";
                if (!file) return;
                try {
                  validateImageFile(file);
                  setError(undefined);
                  setUrl(URL.createObjectURL(file));
                } catch (failure) {
                  setError(new Error(t((failure as Error).message)));
                }
              }}
            />
            <div className="min-w-0 space-y-1.5">
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={pending}
                  onClick={(event) => {
                    finalFocus.current = event.currentTarget;
                    input.current?.click();
                  }}
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
                    onClick={async () => {
                      setError(undefined);
                      try {
                        await onChange(null);
                      } catch (failure) {
                        setError(failure as Error);
                      }
                    }}
                  >
                    <TrashIcon size={14} />
                  </Button>
                )}
              </div>
              {description !== null && (
                <div className="text-xs leading-relaxed text-muted-foreground">
                  {description ??
                    t(
                      "PNG, JPEG or WebP, up to 20 MiB. Crop and compress before saving.",
                    )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
      <ErrorNotice error={error} />
      {url && (
        <ImageCropDialog
          key={url}
          url={url}
          finalFocus={finalFocus}
          onChange={onChange}
          close={() => setUrl(undefined)}
        />
      )}
    </div>
  );
}
