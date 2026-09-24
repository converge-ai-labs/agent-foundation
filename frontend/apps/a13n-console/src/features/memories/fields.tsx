import { PlusIcon, PushPinIcon, XIcon } from "@phosphor-icons/react";
import { Button, FormField, Input, SegmentedControl } from "a13n-ui";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { ListRow, ListRows, ListRowsEmpty } from "../../shared/collection";
import { TextAreaField } from "../../shared/forms";
import { labelsError, type GuideDraft, type GuideMode } from "./form";
import styles from "./memories.module.css";

/** At most this many paths lead a memory's context. */
const MAX_ALWAYS_LOAD = 64;

export function LabelChips({ labels }: { labels: Record<string, string> }) {
  const entries = Object.entries(labels);
  if (!entries.length) return <span>—</span>;
  return (
    <span className={styles.labels}>
      {entries.map(([key, value]) => (
        <span key={key} className={styles.label}>
          {key}: {value}
        </span>
      ))}
    </span>
  );
}

export function LabelsField({
  value,
  onChange,
  readOnly = false,
}: {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const error = labelsError(value);
  return (
    <FormField
      readOnly={readOnly}
      label={t("Labels")}
      description={t("Pairs such as team:docs, separated by commas.")}
      error={error && t(error)}
    >
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
      />
    </FormField>
  );
}

/**
 * The guide tells agents what belongs in the memory and how it is organized.
 * Inherit follows the deployment's guide as it changes; None gives the memory
 * no guide; Custom is the memory's own.
 */
export function GuideField({
  value,
  onChange,
  inherited,
  readOnly = false,
}: {
  value: GuideDraft;
  onChange: (value: GuideDraft) => void;
  /** The deployment's guide, when the memory already inherits it. */
  inherited?: string;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const modes: { value: GuideMode; label: string }[] = [
    { value: "inherit", label: t("Inherit") },
    { value: "custom", label: t("Custom") },
    { value: "none", label: t("None") },
  ];
  return (
    <div className={styles.guide}>
      <div className={styles.guideHeading}>
        <span>{t("Guide")}</span>
        {!readOnly && (
          <SegmentedControl
            label={t("Guide")}
            value={value.mode}
            onValueChange={(mode) =>
              onChange({
                mode: mode as GuideMode,
                // Customizing starts from the guide the memory had.
                text:
                  mode === "custom" && !value.text
                    ? (inherited ?? "")
                    : value.text,
              })
            }
            options={modes}
          />
        )}
      </div>
      {value.mode === "custom" ? (
        <TextAreaField
          label={t("Guide")}
          hideLabel
          readOnly={readOnly}
          required
          rows={6}
          value={value.text}
          onChange={(text) => onChange({ ...value, text })}
          hint={t(
            "What belongs in this memory and how to organize it. Agents read it with their instructions and cannot change it.",
          )}
        />
      ) : value.mode === "none" ? (
        <p className={styles.note}>
          {t("Runs receive no guide for this memory.")}
        </p>
      ) : (
        <>
          <p className={styles.note}>
            {t(
              "Runs use the deployment's guide for file memories, and follow it when it changes.",
            )}
          </p>
          {inherited && (
            <pre
              className={`${styles.inherited} a13n-scrollbar`}
              aria-label={t("Inherited guide")}
            >
              {inherited}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Paths whose full content leads the memory's context in every run. Only
 * people who may change the memory choose them; a path need not exist yet.
 */
export function AlwaysLoadField({
  value,
  onChange,
  files,
  readOnly = false,
}: {
  value: string[];
  onChange: (value: string[]) => void;
  /** The memory's current files, suggested and checked against. */
  files?: readonly string[];
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [path, setPath] = useState("");
  const suggestions = useId();
  const next = path.trim();
  const error = value.includes(next)
    ? t("This path is already listed.")
    : undefined;
  function add() {
    if (!next || error || value.length >= MAX_ALWAYS_LOAD) return;
    onChange([...value, next]);
    setPath("");
  }
  return (
    <div className={styles.alwaysLoad}>
      {value.length ? (
        <ListRows>
          {value.map((item) => (
            <ListRow
              key={item}
              icon={<PushPinIcon size={15} />}
              name={item}
              secondary={
                files && !files.includes(item)
                  ? t("No file at this path yet")
                  : undefined
              }
              actions={
                !readOnly && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("Remove {{name}}", { name: item })}
                    onClick={() =>
                      onChange(value.filter((entry) => entry !== item))
                    }
                  >
                    <XIcon />
                  </Button>
                )
              }
            />
          ))}
        </ListRows>
      ) : (
        <ListRowsEmpty>
          {t("No files are always loaded. Agents start from the index.")}
        </ListRowsEmpty>
      )}
      {!readOnly && (
        <div className={styles.addPath}>
          <FormField label={t("Path to always load")} error={error}>
            <Input
              value={path}
              list={suggestions}
              placeholder="README.md"
              autoComplete="off"
              onChange={(event) => setPath(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.nativeEvent.isComposing)
                  return;
                event.preventDefault();
                add();
              }}
            />
          </FormField>
          <datalist id={suggestions}>
            {files
              ?.filter((file) => !value.includes(file))
              .map((file) => (
                <option key={file} value={file} />
              ))}
          </datalist>
          <Button
            type="button"
            variant="outline"
            disabled={!next || !!error || value.length >= MAX_ALWAYS_LOAD}
            onClick={add}
          >
            <PlusIcon />
            {t("Add path")}
          </Button>
        </div>
      )}
    </div>
  );
}
