import { useQuery } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  Checkbox,
  DisclosureSection,
  FormField,
  Input,
} from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/forms";
import { memoryKeys } from "./api";
import {
  selectSystemPreset,
  guidanceModules,
  guidancePreview,
  validGuidance,
  type FileGuidance,
} from "./guidance";
import styles from "./memories.module.css";

export function FileGuidanceField({
  value,
  onChange,
  readOnly = false,
}: {
  value: FileGuidance;
  onChange: (value: FileGuidance) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const zonesId = useId();
  const presets = useQuery({
    queryKey: [...memoryKeys(workspace.id).root, "guidance-presets"],
    queryFn: () =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/memories/guidance-presets")
        .then(data),
    staleTime: 60_000,
  });
  const selected = presets.data?.items.find(
    (item) => item.id === value.system_preset,
  );
  const overridden = value.system_override !== null;
  const selectedModules = guidanceModules(value);
  const composedGuide = guidancePreview(
    { ...value, system_override: null },
    presets.data,
  );
  return (
    <div className="grid gap-4 min-w-0">
      <ErrorNotice error={presets.error} retry={() => void presets.refetch()} />
      <ChoiceField
        data-field-layout="stacked"
        label={t("Memory organization")}
        description={selected ? t(selected.description) : undefined}
        value={value.system_preset}
        readOnly={readOnly}
        disabled={!presets.data}
        placeholder={t("Loading…")}
        options={(presets.data?.items ?? []).map((item) => ({
          value: item.id,
          label: t(item.name),
        }))}
        onValueChange={(preset) =>
          onChange(
            selectSystemPreset(value, preset as FileGuidance["system_preset"]),
          )
        }
      />
      {overridden && (
        <p className={styles.note}>
          {t(
            "System guide customized. Selecting another preset replaces it and keeps your custom guide.",
          )}
        </p>
      )}
      <div className="grid gap-3">
        <p className="text-xs text-muted-foreground">{t("Guidance modules")}</p>
        {(presets.data?.modules ?? []).map((module) => (
          <label key={module.id} className="flex items-start gap-3">
            <Checkbox
              checked={selectedModules.includes(module.id)}
              disabled={readOnly || overridden}
              onCheckedChange={(checked) =>
                onChange({
                  ...value,
                  modules: checked
                    ? ([
                        ...selectedModules,
                        module.id,
                      ] as FileGuidance["modules"])
                    : (selectedModules.filter(
                        (id) => id !== module.id,
                      ) as FileGuidance["modules"]),
                })
              }
            />
            <span>
              <span className="block text-[13px] font-medium">
                {t(module.name)}
              </span>
              <span className="text-xs text-muted-foreground">
                {t(module.description)}
              </span>
            </span>
          </label>
        ))}
        <p className={styles.note}>
          {t(
            overridden
              ? "The full system override replaces all module guidance."
              : value.modules === null
                ? "Modules follow the selected preset."
                : "Explicit module selection. No modules keeps only the common rules.",
          )}
        </p>
        {!readOnly && value.modules !== null && (
          <div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onChange({ ...value, modules: null })}
            >
              {t("Follow preset modules")}
            </Button>
          </div>
        )}
      </div>
      <TextAreaField
        label={t("Custom guide (optional)")}
        rows={3}
        readOnly={readOnly}
        value={value.custom}
        onChange={(custom) => onChange({ ...value, custom })}
        hint={t(
          "Describe what matters in this memory: the people, projects, decisions or events to remember. The preset supplies the maintenance workflow.",
        )}
      />
      <DisclosureSection
        title={t("Advanced settings")}
        summary={`${t(overridden ? "System guide customized" : "Using preset")} · ${value.timezone}`}
      >
        <div className="grid gap-4 min-w-0">
          <FormField
            label={t("Memory time zone")}
            description={t(
              "Event dates use this time zone, including during background organization.",
            )}
            readOnly={readOnly}
            error={
              validGuidance(value)
                ? undefined
                : t("Enter a valid time zone, such as Asia/Taipei or UTC.")
            }
          >
            <Input
              value={value.timezone}
              list={zonesId}
              required
              autoComplete="off"
              onChange={(event) =>
                onChange({ ...value, timezone: event.target.value })
              }
            />
          </FormField>
          <datalist id={zonesId}>
            {["UTC", ...Intl.supportedValuesOf("timeZone")].map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
          <TextAreaField
            label={t("System guide")}
            rows={10}
            readOnly={readOnly || !selected}
            value={value.system_override ?? composedGuide ?? ""}
            onChange={(text) =>
              onChange({
                ...value,
                system_override: text === composedGuide ? null : text,
              })
            }
            hint={t(
              "Controls how agents read, write and organize memory. Editing replaces the preset rules. Access and version checks still apply.",
            )}
          />
          {!readOnly && overridden && (
            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!selected}
                onClick={() => onChange({ ...value, system_override: null })}
              >
                {t("Restore selected preset")}
              </Button>
            </div>
          )}
          <p className={styles.note}>
            {t(
              "Changes apply to new runs. Existing memory files are not moved or deleted.",
            )}
          </p>
        </div>
      </DisclosureSection>
    </div>
  );
}
