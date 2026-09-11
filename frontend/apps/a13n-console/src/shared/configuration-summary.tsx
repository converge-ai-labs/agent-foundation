import { DisclosureSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { JsonView } from "./form";

export function ConfigurationSummary({
  value,
  schema,
}: {
  value: Record<string, unknown>;
  schema?: Record<string, unknown>;
}) {
  const { t } = useTranslation();
  const properties = (schema?.properties ?? {}) as Record<
    string,
    { title?: string }
  >;
  const label = (key: string) =>
    t(
      properties[key]?.title ??
        key
          .replaceAll("_", " ")
          .replace(/^./, (letter) => letter.toUpperCase()),
    );
  return (
    <dl className="grid gap-3 text-sm">
      {Object.entries(value).map(([key, item]) => (
        <div
          key={key}
          className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-4"
        >
          <dt className="text-muted-foreground">{label(key)}</dt>
          <dd className="min-w-0 wrap-anywhere">
            {item !== null && typeof item === "object" ? (
              <DisclosureSection title={t("View JSON")}>
                <JsonView value={item} />
              </DisclosureSection>
            ) : (
              <>
                {typeof item === "boolean"
                  ? t(item ? "Yes" : "No")
                  : String(item ?? "—")}
              </>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
