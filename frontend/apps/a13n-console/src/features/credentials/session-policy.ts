import type { Schema } from "../../shared/api";
import { parseHosts, validHosts } from "./draft";

export type SessionPolicy = Schema["SessionConfiguration"];
export type SecretReference = Schema["SecretReference"];

/** Text fields retain unfinished lines until the owning form is submitted. */
export function normalizeSessionPolicy(value: SessionPolicy): SessionPolicy {
  if (!value.egress) return value;
  const policy = value.egress;
  return {
    ...value,
    egress: {
      ...policy,
      destinations:
        policy.destinations.mode === "allowlist" &&
        policy.destinations.hosts !== undefined
          ? {
              ...policy.destinations,
              hosts: parseHosts(policy.destinations.hosts.join("\n")),
            }
          : policy.destinations,
      ...(policy.secrets === undefined
        ? {}
        : {
            secrets: policy.secrets.map((reference) => ({
              ...reference,
              inject_hosts: parseHosts(reference.inject_hosts.join("\n")),
            })),
          }),
    },
  };
}

export function validCredentialVariable(env: string) {
  return (
    /^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(env) &&
    !/^(A13N_ENVD_|EIP_)/i.test(env) &&
    ![
      "PATH",
      "SSL_CERT_FILE",
      "SSL_CERT_DIR",
      "REQUESTS_CA_BUNDLE",
      "CURL_CA_BUNDLE",
      "NODE_EXTRA_CA_CERTS",
    ].includes(env)
  );
}

/** Local cross-field checks; resource authority and environment support remain server-owned. */
export function sessionPolicyError(input: SessionPolicy): string | undefined {
  const value = normalizeSessionPolicy(input),
    policy = value.egress,
    secrets = policy?.secrets ?? [];
  const destinations = policy?.destinations;
  if (secrets.length && value.egress_mode !== "controlled")
    return "Session credentials require controlled egress.";
  if (value.egress_mode === "controlled" && !policy)
    return "Choose a destination policy for controlled egress.";
  if (
    policy &&
    (value.egress_mode === "deny" || value.egress_mode === "inherit")
  )
    return "Remove the destination policy before using inherited or denied egress.";
  if (secrets.length > 64)
    return "A Session can bind at most 64 credential variables.";
  if (new Set(secrets.map((item) => item.env)).size !== secrets.length)
    return "Use a different variable name for each credential binding.";
  for (const item of secrets) {
    if (!validCredentialVariable(item.env))
      return "Use a valid, non-reserved environment variable name, such as GH_TOKEN.";
    if (!item.credential_id) return "Choose a credential for each binding.";
    if (!validHosts(item.inject_hosts))
      return "Choose at least one exact injection domain for each binding.";
    if (
      destinations?.mode === "allowlist" &&
      item.inject_hosts.some((host) => !destinations.hosts?.includes(host))
    )
      return "Add every injection domain to the allowed destinations.";
  }
  if (
    policy?.destinations.mode === "allowlist" &&
    (policy.destinations.hosts?.length ?? 0) > 256
  )
    return "A destination allowlist can contain at most 256 hosts.";
}

export function credentialExample(
  reference: SecretReference,
): string | undefined {
  if (
    !validCredentialVariable(reference.env) ||
    !validHosts(reference.inject_hosts)
  )
    return;
  return `curl -H "Authorization: Bearer \${${reference.env}}" https://${reference.inject_hosts[0]}/`;
}
