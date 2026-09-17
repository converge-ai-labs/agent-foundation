import {
  Button,
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
  Checkbox,
  Fieldset,
  FieldsetLegend,
  FormField,
  Input,
  Label,
} from "a13n-ui";

import {
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

import {
  ArrowUpRightIcon,
  PlusIcon,
  PuzzlePieceIcon,
  XIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import { Loading } from "../../shared/feedback";
import styles from "./agents.module.css";
import { EditorSection } from "./section";
import type { useAgentChoices } from "./choices";
import type { AgentConfig } from "./configuration";
import { AgentConnections } from "./connections";

type Skills = NonNullable<AgentConfig["skills"]>;
type Connections = NonNullable<AgentConfig["connection_tools"]>;

export function AgentCapabilities({
  choices,
  skills,
  setSkills,
  connections,
  setConnections,
  readOnly = false,
}: {
  readOnly?: boolean;
  choices: ReturnType<typeof useAgentChoices>;
  skills: Skills;
  setSkills: Dispatch<SetStateAction<Skills>>;
  connections: Connections;
  setConnections: Dispatch<SetStateAction<Connections>>;
}) {
  const { t } = useTranslation(),
    { basePath } = useWorkspace();
  const base = `${basePath}`;
  return (
    <>
      <CapabilityGroup
        readOnly={readOnly}
        title={t("Skills")}
        description={t("Reusable knowledge and procedures.")}
        icon={<PuzzlePieceIcon size={15} />}
        setup={`${base}/skills`}
        loading={choices.isPending}
        choices={availableChoices(
          choices.data?.skills ?? [],
          skills.map((item) => item.skill_key),
        ).map((skill) => {
          const selected = skills.find((item) => item.skill_key === skill.key);
          return {
            ...skill,
            checked: !!selected,
            onChange: (checked) =>
              setSkills((previous) =>
                checked
                  ? [...previous, { skill_key: skill.key }]
                  : previous.filter((item) => item.skill_key !== skill.key),
              ),
            settings: selected && (
              <FormField
                readOnly={readOnly}
                className={styles.pinnedVersion}
                label={t("Pinned version")}
              >
                <Input
                  type="number"
                  min={1}
                  value={selected.version ?? (readOnly ? t("Latest") : "")}
                  placeholder={t("Latest")}
                  onChange={(event) =>
                    setSkills((previous) =>
                      previous.map((item) =>
                        item.skill_key === skill.key
                          ? {
                              ...item,
                              version: event.target.value
                                ? Number(event.target.value)
                                : null,
                            }
                          : item,
                      ),
                    )
                  }
                />
              </FormField>
            ),
          };
        })}
      />
      <AgentConnections
        choices={choices}
        connections={connections}
        setConnections={setConnections}
        readOnly={readOnly}
      />
    </>
  );
}

// Keep configured references editable even when a resource is no longer listed.
function availableChoices(
  available: { key: string; name: string }[],
  selected: string[],
) {
  return [
    ...available.map((item) => ({
      key: item.key,
      name: item.name,
      unavailable: false,
    })),
    ...selected
      .filter((key) => !available.some((item) => item.key === key))
      .map((key) => ({ key, name: key, unavailable: true })),
  ];
}

type CapabilityChoice = {
  key: string;
  name: string;
  unavailable: boolean;
  checked: boolean;
  onChange: (checked: boolean) => void;
  settings?: ReactNode;
};

function CapabilityGroup({
  title,
  description,
  icon,
  choices,
  setup,
  loading,
  readOnly,
}: {
  title: string;
  description: string;
  icon: ReactNode;
  choices: CapabilityChoice[];
  setup: string;
  loading: boolean;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const visible = choices.filter(
    (item) =>
      !item.checked &&
      item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  );
  return (
    <EditorSection title={title} description={description}>
      <div className={styles.capabilityChoices}>
        {choices
          .filter((choice) => choice.checked)
          .map((choice) => (
            <div className={styles.selectedCapability} key={choice.key}>
              <div className={styles.choiceHeading}>
                <span className={styles.capabilityIcon} aria-hidden="true">
                  {icon}
                </span>
                <strong>{choice.name}</strong>
                {choice.unavailable && !loading && (
                  <small>{t("Unavailable")}</small>
                )}
                {!readOnly && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={t("Remove {{name}}", { name: choice.name })}
                    onClick={() => choice.onChange(false)}
                  >
                    {t("Remove")}
                  </Button>
                )}
              </div>
              {choice.settings && (
                <div className={styles.choiceSettings}>{choice.settings}</div>
              )}
            </div>
          ))}
        {readOnly && !choices.some((choice) => choice.checked) && (
          <p className="text-sm text-muted-foreground">{t("None")}</p>
        )}
        {!readOnly && (
          <Collapsible
            className={styles.capabilityPicker}
            open={adding}
            onOpenChange={setAdding}
          >
            <CollapsibleTrigger
              render={<Button variant="secondary" size="sm" />}
            >
              {adding ? <XIcon size={14} /> : <PlusIcon size={14} />}
              {adding
                ? t("Close selection")
                : t("Add {{kind}}", { kind: title })}
            </CollapsibleTrigger>
            <CollapsiblePanel>
              <div className={styles.addCapabilityPanel}>
                {choices.length > 6 && (
                  <FormField
                    className="min-w-0 w-full"
                    label={t("Search {{kind}}", { kind: title })}
                    hideLabel={true}
                  >
                    <Input
                      placeholder={t("Search…")}
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      type="search"
                    />
                  </FormField>
                )}
                <Fieldset className={`a13n-scrollbar ${styles.choiceList}`}>
                  <FieldsetLegend className="sr-only">{title}</FieldsetLegend>
                  {visible.map((choice) => (
                    <div key={choice.key} className={styles.choice}>
                      <div className={styles.choiceHeading}>
                        <Label className="flex items-center gap-2">
                          <Checkbox
                            checked={choice.checked}
                            onCheckedChange={(value) =>
                              choice.onChange(value === true)
                            }
                          />
                          {choice.name}
                        </Label>
                        {choice.unavailable && !loading && (
                          <small>{t("Unavailable")}</small>
                        )}
                      </div>
                    </div>
                  ))}
                  {!choices.length && loading && (
                    <Loading variant="list" rows={3} />
                  )}
                  {!choices.length && !loading && (
                    <p className={styles.capabilityEmpty}>
                      {t("None available in this workspace.")}
                    </p>
                  )}
                  {!!choices.length && !visible.length && (
                    <p className={styles.capabilityEmpty}>
                      {t(
                        search
                          ? "No matching capabilities"
                          : "All available resources added",
                      )}
                    </p>
                  )}
                </Fieldset>
                <Link
                  className={styles.setupCapability}
                  to={setup}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("Set up {{kind}}", { kind: title })}
                  <ArrowUpRightIcon size={12} aria-hidden="true" />
                  <span className="visually-hidden">
                    {t("Opens in a new tab")}
                  </span>
                </Link>
              </div>
            </CollapsiblePanel>
          </Collapsible>
        )}
      </div>
    </EditorSection>
  );
}
