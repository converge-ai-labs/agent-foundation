/** The address shown with a person, never their sign-in or authorization identifier. */
export function displayEmail(
  person?: { email?: string | null; oidc_email?: string | null } | null,
): string | undefined {
  return person?.email || person?.oidc_email || undefined;
}
