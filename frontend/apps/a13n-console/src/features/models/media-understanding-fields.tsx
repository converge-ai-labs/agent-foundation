import { ArrowBendUpLeftIcon } from "@phosphor-icons/react";
import { SearchPicker, SettingsRow } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import {
  mediaDefaultsQuery,
  workspaceModelsQuery,
  workspaceProvidersQuery,
} from "./api";
import { ModelIcon, providerBrand } from "./model-icon";
import styles from "./models.module.css";

export type MediaKind = "image" | "video" | "audio";
/** The Model key chosen for each kind; an unset kind inherits. */
export type MediaSelection = Schema["MediaUnderstandingSelection"];

/** What a picker needs to show a Model as itself, wherever it was loaded. */
export type ModelIdentity = Pick<
  Schema["Model"],
  "key" | "name" | "provider_id" | "catalog_ref"
> & { config: Pick<Schema["Model"]["config"], "model_name"> };

/**
 * One row per media kind: its label and the content it covers, the accessible
 * name of its picker, the hint when no Model qualifies, the warning when the
 * saved one no longer does, and the badge the Models list shows.
 */
export const mediaKinds: {
  kind: MediaKind;
  label: string;
  description: string;
  field: string;
  empty: string;
  unavailable: string;
  badge: string;
}[] = [
  {
    kind: "image",
    label: "Image",
    description: "Screenshots, photos, and diagrams the agent opens",
    field: "Image understanding",
    empty: "No enabled model declares image understanding.",
    unavailable:
      "Saved model {{key}} is disabled or no longer declares image understanding. Choose another.",
    badge: "Image default",
  },
  {
    kind: "video",
    label: "Video",
    description: "Screen recordings and clips the agent opens",
    field: "Video understanding",
    empty: "No enabled model declares video understanding.",
    unavailable:
      "Saved model {{key}} is disabled or no longer declares video understanding. Choose another.",
    badge: "Video default",
  },
  {
    kind: "audio",
    label: "Audio",
    description: "Voice notes and recordings the agent opens",
    field: "Audio understanding",
    empty: "No enabled model declares audio understanding.",
    unavailable:
      "Saved model {{key}} is disabled or no longer declares audio understanding. Choose another.",
    badge: "Audio default",
  },
];

/** Exactly the three keys, so a saved representation never leaks its other fields. */
function withKind(
  value: MediaSelection,
  kind: MediaKind,
  key: string | null,
): MediaSelection {
  return {
    image: value.image ?? null,
    video: value.video ?? null,
    audio: value.audio ?? null,
    [kind]: key,
  };
}

/** The kinds a selection names, ignoring the ones it leaves inherited. */
export function mediaSelected(value: MediaSelection = {}) {
  return mediaKinds
    .filter((entry) => value[entry.kind])
    .map(({ kind }) => kind);
}

/**
 * A Model is read by its name and key, so a picker's popup keeps a comfortable
 * width of its own instead of wrapping inside a narrow trigger.
 */
export const modelPopupWidth = "min-w-80";

type ModelProvider = Pick<Schema["Provider"], "name" | "type" | "config">;

/**
 * One Model as itself: the family logo, the name people recognize it by, and
 * the key and connection that tell two similar Models apart. Every Model
 * picker in Console builds its options here, so a Model reads the same
 * wherever it is chosen. Given the Provider, a Model with no family of its
 * own carries the Provider's brand; given only its name, it has none.
 */
export function modelOption(
  model: ModelIdentity,
  provider?: ModelProvider | string,
) {
  const { name, brand } =
    typeof provider === "string"
      ? { name: provider, brand: undefined }
      : { name: provider?.name, brand: providerBrand(provider) };
  return {
    value: model.key,
    label: model.name,
    description: [model.key, name].filter(Boolean).join(" · "),
    icon: (
      <ModelIcon
        upstream={model.config.model_name}
        catalogRef={model.catalog_ref}
        provider={brand}
        size={20}
      />
    ),
    keywords: [model.key, model.config.model_name],
  };
}

/** What an inherited choice carries where the Models beside it carry a logo. */
export function InheritIcon() {
  return (
    <ArrowBendUpLeftIcon
      size={20}
      className={styles.mediaGlyph}
      aria-hidden="true"
    />
  );
}

/**
 * The Workspace Models a picker can offer: every enabled Model from an enabled
 * Provider, the Providers that name them, and the ones declaring each media
 * understanding capability.
 */
export function useMediaUnderstandingChoices() {
  const { workspace } = useWorkspace(),
    client = useClient();
  const models = useQuery(workspaceModelsQuery(client, workspace.id));
  const providers = useQuery(workspaceProvidersQuery(client, workspace.id));
  const enabledProviders = new Set(
    providers.data
      ?.filter((provider) => provider.enabled)
      .map((provider) => provider.id),
  );
  const usable = (models.data ?? []).filter(
    (model) => model.enabled && enabledProviders.has(model.provider_id),
  );
  const provider = (id: string) =>
    providers.data?.find((entry) => entry.id === id);
  return {
    error: models.error ?? providers.error,
    isPending: models.isPending || providers.isPending,
    eligible: (kind: MediaKind) =>
      usable.filter((model) =>
        model.config.characteristics?.capabilities?.includes(
          `${kind}_understanding`,
        ),
      ),
    /** Any known Model, including one a selection kept after it stopped qualifying. */
    find: (key: string) =>
      (models.data ?? []).find((model) => model.key === key),
    provider,
    providerName: (id: string) => provider(id)?.name,
  };
}

/** The Model each kind falls back to at the Workspace level, named. */
export function useWorkspaceMediaDefault() {
  const { workspace } = useWorkspace(),
    client = useClient();
  const defaults = useQuery(mediaDefaultsQuery(client, workspace.id));
  const { find } = useMediaUnderstandingChoices();
  return (kind: MediaKind) => {
    const key = defaults.data?.value[kind];
    return key ? (find(key)?.name ?? key) : undefined;
  };
}

/** What a closed disclosure says about a selection: the kinds it names, or what it all falls back to. */
export function useMediaSummary() {
  const { t } = useTranslation();
  const { find } = useMediaUnderstandingChoices();
  return (value: MediaSelection, inherited: string) => {
    const chosen = mediaKinds.flatMap((entry) => {
      const key = value[entry.kind];
      return key ? [`${t(entry.label)} · ${find(key)?.name ?? key}`] : [];
    });
    if (!chosen.length) return inherited;
    const named = chosen.join(", ");
    return chosen.length === mediaKinds.length
      ? named
      : t("{{kinds}}, others inherited", { kinds: named });
  };
}

/**
 * The three media understanding rows, controlled and save-agnostic: the
 * Workspace defaults autosave each row, while an Agent or Run composes them
 * into its own draft. Each row states what it covers, names the Model it would
 * otherwise inherit, and keeps a dead saved value visible instead of silently
 * dropping it.
 */
export function MediaUnderstandingFields({
  value,
  onChange,
  inherit,
  explainEmpty = true,
  disabled = false,
  pendingKind,
}: {
  value: MediaSelection;
  /** The whole next selection, plus the kind that changed for owners that save one row. */
  onChange: (value: MediaSelection, kind: MediaKind) => void;
  /**
   * What an unset kind falls back to: a Workspace default, an Agent choice, or
   * nothing. The description names the Model that would actually read the kind.
   */
  inherit: {
    label: string;
    describe?: (kind: MediaKind) => string | undefined;
    /** The choice's mark: the inherit mark unless set, `null` where nothing is inherited. */
    icon?: ReactNode;
  };
  /**
   * Whether a kind no enabled Model declares says so in its row. The Workspace
   * settings page owns that explanation; a dialog leaves it to the empty picker.
   */
  explainEmpty?: boolean;
  disabled?: boolean;
  /** The row whose value the owner is committing; it reads as saving. */
  pendingKind?: MediaKind;
}) {
  const { basePath } = useWorkspace(),
    { t } = useTranslation();
  const choices = useMediaUnderstandingChoices();
  return (
    <>
      <ErrorNotice error={choices.error} />
      {mediaKinds.map((entry) => {
        const { kind, field } = entry;
        const eligible = choices.eligible(kind);
        const selected = value[kind] ?? "";
        const unavailable =
          !!selected && !eligible.some((model) => model.key === selected);
        // A selection the Workspace can no longer honour stays on show, named as far as it can be.
        const stale = unavailable ? choices.find(selected) : undefined;
        const note = unavailable
          ? {
              tone: "warning",
              text: t(entry.unavailable, { key: selected }),
            }
          : !explainEmpty || choices.isPending || eligible.length
            ? undefined
            : { tone: undefined, text: t(entry.empty) };
        return (
          <SettingsRow
            key={kind}
            label={t(entry.label)}
            description={
              <>
                {t(entry.description)}
                {note && (
                  <span className={styles.mediaNote} data-tone={note.tone}>
                    {note.text}{" "}
                    <Link
                      className={styles.mediaLink}
                      to={`${basePath}/models`}
                    >
                      {t("Manage models")}
                    </Link>
                  </span>
                )}
              </>
            }
          >
            <div className={styles.mediaControl}>
              {pendingKind === kind && (
                <span role="status" className={styles.mediaStatus}>
                  {t("Saving…")}
                </span>
              )}
              <div className={styles.mediaPicker}>
                <SearchPicker
                  id={`media-understanding-${kind}`}
                  label={t(field)}
                  placeholder={inherit.label}
                  emptyMessage={t(entry.empty)}
                  value={selected}
                  disabled={disabled || choices.isPending || !!choices.error}
                  popupClassName={modelPopupWidth}
                  groups={[
                    {
                      label: t(field),
                      options: [
                        {
                          value: "",
                          label: inherit.label,
                          description: inherit.describe?.(kind),
                          icon:
                            inherit.icon === undefined ? (
                              <InheritIcon />
                            ) : (
                              inherit.icon
                            ),
                        },
                        ...(unavailable
                          ? [
                              {
                                value: selected,
                                label: stale?.name ?? selected,
                                description: `${selected} · ${t("Unavailable")}`,
                                disabled: true,
                                icon: (
                                  <ModelIcon
                                    upstream={stale?.config.model_name ?? ""}
                                    catalogRef={stale?.catalog_ref}
                                    provider={providerBrand(
                                      stale &&
                                        choices.provider(stale.provider_id),
                                    )}
                                    size={20}
                                  />
                                ),
                              },
                            ]
                          : []),
                        ...eligible.map((model) =>
                          modelOption(
                            model,
                            choices.provider(model.provider_id),
                          ),
                        ),
                      ],
                    },
                  ]}
                  onValueChange={(next) => {
                    if (next !== selected)
                      onChange(withKind(value, kind, next || null), kind);
                  }}
                />
              </div>
            </div>
          </SettingsRow>
        );
      })}
    </>
  );
}
