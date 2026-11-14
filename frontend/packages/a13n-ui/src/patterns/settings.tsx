import type { ReactNode } from "react";
import { useId } from "react";
import { cn } from "../lib/utils";

export function SettingsSection({
  title,
  description,
  children,
  variant = "grouped",
  className,
}: {
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  variant?: "grouped" | "outlined" | "plain";
  className?: string;
}) {
  const id = useId();
  return (
    <section
      className={cn("@container text-sm", className)}
      aria-labelledby={title ? id : undefined}
    >
      {title && (
        <div className="mb-2">
          <h3 id={id} data-slot="settings-section-title">
            {title}
          </h3>
          {description && (
            <p
              className={cn(
                "mt-1 text-(--a13n-secondary)",
                variant === "outlined" ? "text-xs leading-relaxed" : "text-sm",
              )}
            >
              {description}
            </p>
          )}
        </div>
      )}
      <div
        className={cn(
          "[&>form]:py-5",
          variant === "grouped"
            ? "rounded-[12px] bg-muted px-4 py-0.5 [&>*+*]:border-(--a13n-separator) [&>*+*]:border-t"
            : variant === "outlined"
              ? "rounded-[12px] border border-border bg-background px-4 [&>*+*]:border-(--a13n-separator) [&>*+*]:border-t"
              : "",
        )}
        data-slot="settings-section-content"
        data-variant={variant}
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
  className,
  children,
}: {
  label: ReactNode;
  description?: ReactNode;
  controlId?: string;
  stackOnNarrow?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      data-slot="settings-row"
      className={cn(
        "flex min-w-0 items-center justify-between gap-6 py-4",
        stackOnNarrow &&
          "@max-[420px]:flex-col @max-[420px]:items-stretch @max-[420px]:gap-2.5",
        className,
      )}
    >
      <div className="min-w-0 wrap-anywhere">
        {controlId ? (
          <label data-slot="settings-row-label" htmlFor={controlId}>
            {label}
          </label>
        ) : (
          <span data-slot="settings-row-label">{label}</span>
        )}
        {description && (
          <p
            id={controlId ? `${controlId}-description` : undefined}
            data-slot="settings-row-description"
            className="mt-1"
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
