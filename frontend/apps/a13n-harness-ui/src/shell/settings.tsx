import { useId, type ComponentProps } from "react";
import {
  ChoiceField,
  Input,
  Switch,
  SettingsRow as SharedSettingsRow,
  SettingsSection as SharedSettingsSection,
} from "a13n-ui";
import styles from "./settings.module.css";

/** The same label/value rhythm in pages, dialogs, and compact option panels. */
export function SettingsRow(props: ComponentProps<typeof SharedSettingsRow>) {
  return (
    <SharedSettingsRow
      stackOnNarrow={false}
      {...props}
      className={[styles.row, props.className].filter(Boolean).join(" ")}
    />
  );
}

export function SettingsSection(
  props: ComponentProps<typeof SharedSettingsSection>,
) {
  return (
    <SharedSettingsSection
      variant="grouped"
      {...props}
      className={[styles.section, props.className].filter(Boolean).join(" ")}
    />
  );
}

export function SettingsChoice({
  label,
  description,
  hideLabel,
  layout = "row",
  ...props
}: ComponentProps<typeof ChoiceField> & { layout?: "row" | "stacked" }) {
  const generatedId = useId();
  const id = props.id ?? generatedId;
  // Existing rows already provide their label; never nest a row inside a row.
  if (hideLabel || props.variant === "filter" || layout === "stacked")
    return (
      <ChoiceField
        {...props}
        label={label}
        description={description}
        hideLabel={hideLabel}
      />
    );
  return (
    <SettingsRow label={label} description={description} controlId={id}>
      <ChoiceField
        {...props}
        id={id}
        label={label}
        hideLabel
        aria-describedby={
          description ? `${id}-description` : props["aria-describedby"]
        }
      />
    </SettingsRow>
  );
}

export function SettingsTextField({
  label,
  description,
  value,
  onChange,
  type = "text",
  disabled = false,
}: {
  label: string;
  description?: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <SettingsRow label={label} description={description} controlId={id}>
      <Input
        id={id}
        className={styles.textInput}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        type={type}
        disabled={disabled}
        autoComplete="off"
        aria-describedby={description ? `${id}-description` : undefined}
      />
    </SettingsRow>
  );
}

export function SettingsToggle({
  label,
  description,
  ...props
}: Omit<ComponentProps<typeof Switch>, "id"> & {
  label: string;
  description?: string;
}) {
  const id = useId();
  return (
    <SettingsRow label={label} description={description} controlId={id}>
      <Switch
        {...props}
        id={id}
        aria-describedby={description ? `${id}-description` : undefined}
      />
    </SettingsRow>
  );
}
