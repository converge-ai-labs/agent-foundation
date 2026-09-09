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
        <div className="mb-3">
          <h3 id={id} className="font-medium">
            {title}
          </h3>
          {description && (
            <p className="mt-1 text-muted-foreground">{description}</p>
          )}
        </div>
      )}
      <div
        className={cn(
          "[&>form]:py-5",
          variant === "grouped" ? "rounded-xl border bg-card px-4" : "border-t",
        )}
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
  children,
}: {
  label: string;
  description?: ReactNode;
  controlId?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-6 py-4 not-first:border-t @max-[420px]:flex-col @max-[420px]:items-stretch @max-[420px]:gap-2.5">
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
      <div className="flex max-w-[55%] shrink-0 items-center justify-end @max-[420px]:max-w-full @max-[420px]:justify-start">
        {children}
      </div>
    </div>
  );
}
