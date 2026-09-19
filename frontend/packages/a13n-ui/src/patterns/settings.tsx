import type { ReactNode } from "react";
import { useId } from "react";
import { cn } from "../lib/utils";

export function SettingsSection({
  title,
  description,
  children,
  variant = "grouped",
}: {
  title?: string;
  description?: ReactNode;
  children: ReactNode;
  variant?: "grouped" | "plain";
}) {
  const id = useId();
  return (
    <section
      className="@container text-sm"
      aria-labelledby={title ? id : undefined}
    >
      {title && (
        <div className="mb-2.5 px-1">
          <h3 id={id} className="font-medium text-[15px] text-foreground">
            {title}
          </h3>
          {description && (
            <p className="mt-1 text-[12.5px] text-muted-foreground">
              {description}
            </p>
          )}
        </div>
      )}
      <div
        className={cn(
          "[&>form]:py-5",
          variant === "grouped"
            ? "rounded-[12px] bg-muted px-4 py-0.5 [&>*+*]:border-border/75 [&>*+*]:border-t"
            : "",
        )}
        data-slot="settings-section-content"
      >
        {children}
      </div>
    </section>
  );
}
export function SettingsRow({
  label,
  description,
  controlId,
  stackOnNarrow = true,
  children,
}: {
  label: string;
  description?: ReactNode;
  controlId?: string;
  stackOnNarrow?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center justify-between gap-6 py-4",
        stackOnNarrow &&
          "@max-[420px]:flex-col @max-[420px]:items-stretch @max-[420px]:gap-2.5",
      )}
    >
      <div className="min-w-0 wrap-anywhere">
        {controlId ? (
          <label htmlFor={controlId}>{label}</label>
        ) : (
          <span>{label}</span>
        )}
        {description && (
          <p
            id={controlId ? `${controlId}-description` : undefined}
            className="mt-1 text-xs text-muted-foreground"
          >
            {description}
          </p>
        )}
      </div>
      <div
        className={cn(
          "flex max-w-[55%] shrink-0 items-center justify-end",
          stackOnNarrow && "@max-[420px]:max-w-full @max-[420px]:justify-start",
        )}
      >
        {children}
      </div>
    </div>
  );
}
