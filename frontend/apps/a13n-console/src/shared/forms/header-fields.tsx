import { PlusIcon, XIcon } from "@phosphor-icons/react";
import { Button, FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";

export type HeaderDraft = {
  id: string;
  name: string;
  value: string;
  savedName?: string;
};

export function serializeHeaders(rows: HeaderDraft[], savedNames: string[]) {
  const updates: Record<string, string | null> = {};
  const names = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim().toLowerCase();
    if (!name || names.has(name))
      throw new Error("Header names must be nonempty and unique.");
    names.add(name);
    if (row.value) updates[name] = row.value;
    else if (row.savedName !== name)
      throw new Error("Enter a value for each new header.");
  }
  for (const name of savedNames) {
    if (!names.has(name)) updates[name] = null;
  }
  return updates;
}

export function HeaderFields({
  rows,
  onChange,
  disabled = false,
  maxRows = 32,
}: {
  disabled?: boolean;
  maxRows?: number;
  rows: HeaderDraft[];
  onChange: (rows: HeaderDraft[]) => void;
}) {
  const { t } = useTranslation();
  function update(id: string, patch: Partial<HeaderDraft>) {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  }
  return (
    <div className="grid gap-2">
      {rows.some((row) => row.savedName) && (
        <p className="text-xs text-muted-foreground">
          {t(
            "Header values are stored securely and never shown. Leave a saved value empty to keep it.",
          )}
        </p>
      )}
      {rows.map((row, index) => (
        <div
          key={row.id}
          className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
        >
          {/* The first row names the columns; later rows keep their names for
              assistive technology only. */}
          <FormField
            className="min-w-0 max-sm:col-span-2"
            label={
              <>
                {t("Header name")}
                <span className={index ? undefined : "sr-only"}>
                  {" "}
                  {index + 1}
                </span>
              </>
            }
            hideLabel={index > 0}
          >
            <Input
              disabled={disabled}
              required
              placeholder="X-API-Key"
              value={row.name}
              autoComplete="off"
              onChange={(event) => update(row.id, { name: event.target.value })}
            />
          </FormField>
          <FormField
            className="min-w-0"
            label={
              <>
                {t("Header value")}
                <span className={index ? undefined : "sr-only"}>
                  {" "}
                  {index + 1}
                </span>
              </>
            }
            hideLabel={index > 0}
          >
            <Input
              disabled={disabled}
              required={!row.savedName}
              type="password"
              value={row.value}
              autoComplete="new-password"
              placeholder={
                row.savedName === row.name.trim().toLowerCase()
                  ? t("Saved value")
                  : undefined
              }
              onChange={(event) =>
                update(row.id, { value: event.target.value })
              }
            />
          </FormField>
          <Button
            type="button"
            disabled={disabled}
            variant="ghost"
            size="icon"
            className="self-end text-muted-foreground hover:text-destructive-foreground"
            aria-label={t("Remove header") + ` ${index + 1}`}
            onClick={() => onChange(rows.filter((item) => item.id !== row.id))}
          >
            <XIcon aria-hidden />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="justify-self-start"
        disabled={disabled || rows.length >= maxRows}
        onClick={() =>
          onChange([...rows, { id: crypto.randomUUID(), name: "", value: "" }])
        }
      >
        <PlusIcon aria-hidden />
        {t("Add header")}
      </Button>
    </div>
  );
}
