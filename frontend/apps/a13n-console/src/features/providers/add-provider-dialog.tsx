import { PlusIcon } from "@phosphor-icons/react";
import { Button, Input, ModalFrame } from "a13n-ui";
import { useState, type ReactElement, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { BrandTitle, CatalogTile, CatalogTiles } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { ProviderIcon } from "../../shared/identity";

export interface ProviderDefinition {
  type: string;
  display_name: string;
}

/**
 * Catalog-first provider creation, shared by every category: choose the service
 * from a searchable tile grid, then connect it in a step that carries its
 * brand. Categories supply their definitions, the tile hint, and the form body.
 */
export function AddProviderDialog<D extends ProviderDefinition>({
  definitions,
  error,
  open,
  onOpenChange,
  trigger,
  finalFocus,
  title,
  description,
  note,
  hint,
  featured,
  connectTitle,
  connectDescription,
  onChoose,
  children,
}: {
  definitions?: readonly D[];
  error?: unknown;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` suppresses the built-in trigger for a controlled dialog. */
  trigger?: ReactElement | null;
  finalFocus?: React.RefObject<HTMLElement | null>;
  title?: string;
  description?: string;
  /** Footnote under the catalog. */
  note?: string;
  /** Secondary line on a tile — what the service asks you for. */
  hint?: (definition: D) => string;
  /** Types listed first; the rest keep the catalog order. */
  featured?: readonly string[];
  connectTitle?: (definition: D) => ReactNode;
  connectDescription?: (definition: D) => string | undefined;
  /** Called whenever the chosen service changes; "" when stepping back. */
  onChoose?: (type: string) => void;
  children: (definition: D, back: () => void) => ReactNode;
}) {
  const { t } = useTranslation();
  const [type, setType] = useState("");
  const chosen = definitions?.find((item) => item.type === type);
  function choose(value: string) {
    setType(value);
    onChoose?.(value);
  }
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value, details) => {
        onOpenChange(value);
        if (!value && !details?.isCanceled) choose("");
      }}
      size="lg"
      placement="top"
      closeLabel={t("Close")}
      finalFocus={finalFocus}
      trigger={
        trigger === null
          ? undefined
          : (trigger ?? (
              <Button type="button">
                <PlusIcon aria-hidden="true" />
                {t("Add provider")}
              </Button>
            ))
      }
      title={
        chosen
          ? (connectTitle?.(chosen) ?? <ConnectTitle definition={chosen} />)
          : (title ?? t("Add provider"))
      }
      description={
        chosen
          ? connectDescription?.(chosen)
          : (description ?? t("Choose the service to connect."))
      }
    >
      {open &&
        (error ? (
          <ErrorNotice error={error} />
        ) : !definitions ? (
          <Loading variant="form" rows={3} />
        ) : chosen ? (
          children(chosen, () => choose(""))
        ) : (
          <ProviderCatalog
            definitions={definitions}
            featured={featured}
            hint={hint}
            note={note}
            onChoose={choose}
          />
        ))}
    </ModalFrame>
  );
}

function ConnectTitle({ definition }: { definition: ProviderDefinition }) {
  const { t } = useTranslation();
  return (
    <BrandTitle mark={<ProviderIcon type={definition.type} />}>
      {t("Connect {{provider}}", { provider: definition.display_name })}
    </BrandTitle>
  );
}

/** Searchable tile grid of the services a category can connect. */
export function ProviderCatalog<D extends ProviderDefinition>({
  definitions,
  featured = [],
  hint,
  note,
  onChoose,
}: {
  definitions: readonly D[];
  featured?: readonly string[];
  hint?: (definition: D) => string;
  note?: string;
  onChoose: (type: string) => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const term = query.trim().toLocaleLowerCase();
  const rank = (definition: D) => {
    const index = featured.indexOf(definition.type);
    return index === -1 ? featured.length : index;
  };
  const visible = definitions
    .filter(
      (definition) =>
        !term ||
        `${definition.display_name} ${definition.type}`
          .toLocaleLowerCase()
          .includes(term),
    )
    .sort((a, b) => rank(a) - rank(b));
  return (
    <CatalogTiles
      search={
        definitions.length > 6 ? (
          <Input
            type="search"
            autoFocus
            aria-label={t("Search providers…")}
            placeholder={t("Search providers…")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        ) : undefined
      }
      empty={t("No matching providers")}
      note={note}
    >
      {visible.length
        ? visible.map((definition) => (
            <CatalogTile
              key={definition.type}
              icon={<ProviderIcon type={definition.type} />}
              name={definition.display_name}
              detail={hint && t(hint(definition))}
              onClick={() => onChoose(definition.type)}
            />
          ))
        : undefined}
    </CatalogTiles>
  );
}
