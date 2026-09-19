/** Preserve only the terminal's pairing identity across login and Workspace selection. */
export function pairingFromSearch(search: string): string | undefined {
  const value = new URLSearchParams(search).get("envd_pairing");
  return value && /^pair-[0-9a-f]{24}$/.test(value) ? value : undefined;
}

export function pairingSearch(search: string): string {
  const pairing = pairingFromSearch(search);
  return pairing ? `?envd_pairing=${pairing}` : "";
}
