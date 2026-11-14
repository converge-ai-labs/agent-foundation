import { useId, useState } from "react";
import { Button, Input } from "a13n-ui";
import {
  ArrowCounterClockwise,
  ChatCircle,
  FolderSimple,
} from "@phosphor-icons/react";
import { useAppearance } from "../appearance/react";
import {
  regions,
  sizeRules,
  validSize,
  type SizeRegion,
  type Theme,
} from "../appearance/model";
import { useTranslation } from "../i18n";
import { PageHeader } from "../shell/ui";
import {
  SettingsChoice,
  SettingsRow,
  SettingsSection,
} from "../shell/settings";
import markdown from "../conversations/markdown.module.css";
import navigation from "../shell/navigation-row.module.css";
import styles from "./appearance.module.css";

const labels: Record<SizeRegion, string> = {
  navigation: "Navigation",
  interface: "Interface",
  conversation: "Conversation",
  composer: "Composer",
  code: "Code",
  terminal: "Terminal",
};
const descriptions: Record<SizeRegion, string> = {
  navigation: "Projects, chats and settings navigation",
  interface: "Menus, buttons, forms and tool labels",
  conversation: "Messages, thoughts and Markdown tables",
  composer: "Message input text",
  code: "Code blocks, source editors, diffs and logs",
  terminal: "Terminal text and grid",
};
function SizeControl({ region }: { region: SizeRegion }) {
  const { t } = useTranslation();
  const { appearance, store } = useAppearance();
  const value = appearance.sizes[region];
  const rule = sizeRules[region];
  const id = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft !== null && validSize(region, Number(draft)))
      store.setSize(region, Number(draft));
    setDraft(null);
  };
  return (
    <SettingsRow
      label={t(labels[region])}
      description={t(descriptions[region])}
      controlId={id}
      className={styles.sizeRow}
    >
      <div className={styles.sizeControl}>
        <Input
          id={id}
          type="number"
          inputMode="numeric"
          min={rule.min}
          max={rule.max}
          step={1}
          value={draft ?? value}
          onChange={(event) => {
            const text = event.target.value;
            setDraft(text);
            if (validSize(region, Number(text)))
              store.setSize(region, Number(text));
          }}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === "Enter") commit();
          }}
        />
        <span className={styles.unit}>px</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t("Reset {{region}} font size", {
            region: t(labels[region]),
          })}
          className={
            value === rule.default && draft === null
              ? styles.resetHidden
              : undefined
          }
          disabled={value === rule.default && draft === null}
          tabIndex={value === rule.default && draft === null ? -1 : undefined}
          aria-hidden={
            value === rule.default && draft === null ? true : undefined
          }
          onClick={() => {
            setDraft(null);
            store.setSize(region, rule.default);
          }}
        >
          <ArrowCounterClockwise size={16} />
        </Button>
      </div>
    </SettingsRow>
  );
}
export function AppearanceSettings() {
  const { t } = useTranslation();
  const { appearance, store, saveFailed } = useAppearance();
  return (
    <div className={styles.page}>
      <PageHeader
        title={t("Appearance")}
        description={t("Your theme and text sizes, saved in this browser.")}
      />
      <SettingsSection title={t("Theme")}>
        <SettingsChoice
          label={t("Color theme")}
          value={appearance.theme}
          onValueChange={(value) => store.setTheme(value as Theme)}
          options={[
            { value: "light", label: t("Light") },
            { value: "dark", label: t("Dark") },
            { value: "system", label: t("System") },
          ]}
        />
      </SettingsSection>
      <SettingsSection
        title={t("Text sizes")}
        description={t(
          "Adjust each area independently. System fonts stay unchanged.",
        )}
      >
        {regions.map((region) => (
          <SizeControl key={region} region={region} />
        ))}
      </SettingsSection>
      {saveFailed && (
        <p role="status" className={styles.saveError}>
          {t(
            "Couldn't save appearance in this browser. Changes apply to this page only.",
          )}
        </p>
      )}
      <section
        aria-label={t("Appearance preview")}
        className={styles.previewSection}
      >
        <h2>{t("Preview")}</h2>
        <div className={styles.preview}>
          <div className={styles.previewNav}>
            <div className={navigation.row}>
              <FolderSimple size={16} />
              {t("My project")}
            </div>
            <div className={`${navigation.row} ${styles.selected}`}>
              <ChatCircle size={16} />
              {t("A new idea")}
            </div>
          </div>
          <div className={styles.previewBody}>
            <div className={markdown.markdown}>
              <h3>{t("Make room for your ideas")}</h3>
              <p>{t("Read, write and explore at a size that feels right.")}</p>
              <pre>
                <code>{'const greeting = "Hello, 世界";'}</code>
              </pre>
            </div>
            <div className={styles.previewTerminal}>
              <span aria-hidden="true">$ </span>echo "Hello, 世界"
            </div>
            <div className={styles.previewComposer}>
              {t("What would you like to work on?")}
            </div>
            <div className={styles.previewMetadata}>
              {t("Ready when you are")}
            </div>
          </div>
        </div>
      </section>
      <div>
        <Button variant="outline" onClick={store.reset}>
          {t("Reset appearance")}
        </Button>
      </div>
    </div>
  );
}
