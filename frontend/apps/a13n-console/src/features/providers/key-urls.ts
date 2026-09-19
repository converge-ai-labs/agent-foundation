/** Where a provider hands out the secret its connect step asks for. */
export type ProviderKeyLinkTarget = { href: string; label?: string };

/**
 * Each Provider definition declares its own setup page, so the Console names no
 * vendor: the link follows the installed definition, built-in or plugin alike.
 */
export function providerKeyLink(
  definition?: {
    setup_url?: string | null;
    setup_label?: string | null;
  } | null,
): ProviderKeyLinkTarget | undefined {
  if (!definition?.setup_url) return undefined;
  return {
    href: definition.setup_url,
    ...(definition.setup_label ? { label: definition.setup_label } : {}),
  };
}
