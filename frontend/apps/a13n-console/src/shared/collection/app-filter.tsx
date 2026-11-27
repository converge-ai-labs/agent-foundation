import { ChoiceField } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useInstalledApps } from "../../app-plugins/context";

export function AppFilter({
  value,
  onChange,
}: {
  value?: string;
  onChange: (value: string | undefined) => void;
}) {
  const { t } = useTranslation();
  const catalog = useInstalledApps();
  if (catalog.length === 0 && !value) return null;
  const options = catalog.map((app) => ({
    value: app.app_id,
    label: app.name,
  }));
  if (value && !options.some((app) => app.value === value))
    options.push({ value, label: value });
  return (
    <ChoiceField
      label={t("App")}
      variant="filter"
      value={value ?? ""}
      options={[{ value: "", label: t("All apps") }, ...options]}
      onValueChange={(appId) => onChange(appId || undefined)}
    />
  );
}

export function useAppFilterParam() {
  const [params, setParams] = useSearchParams();
  return [
    params.get("app_id") || undefined,
    (value: string | undefined) =>
      setParams((previous) => {
        const next = new URLSearchParams(previous);
        if (value) next.set("app_id", value);
        else next.delete("app_id");
        return next;
      }),
  ] as const;
}
