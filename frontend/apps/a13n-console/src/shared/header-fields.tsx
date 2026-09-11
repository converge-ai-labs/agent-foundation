import { TrashIcon } from "@phosphor-icons/react";
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
    <div className="grid gap-3">
      {rows.some((row) => row.savedName) && (
        <p className="text-sm text-muted-foreground">
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
          <FormField
            className="min-w-0 max-sm:col-span-2"
            label={t("Header name") + ` ${index + 1}`}
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
            label={t("Header value") + ` ${index + 1}`}
          >
            <Input
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
            variant="destructive"
            size="icon"
            className="self-end"
            aria-label={t("Remove header") + ` ${index + 1}`}
            onClick={() => onChange(rows.filter((item) => item.id !== row.id))}
          >
            <TrashIcon aria-hidden />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled || rows.length >= maxRows}
        onClick={() =>
          onChange([...rows, { id: crypto.randomUUID(), name: "", value: "" }])
        }
      >
        {t("Add header")}
      </Button>
    </div>
  );
}
