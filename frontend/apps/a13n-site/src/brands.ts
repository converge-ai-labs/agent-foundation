import { brands } from "a13n-ui/brand/brands";

// Names on the page that are not their registry identity in lower case.
const IDENTITIES: Record<string, string> = {
  "Amazon Bedrock": "aws_bedrock",
  "Azure OpenAI": "azure_openai",
  "Fireworks AI": "fireworks",
  "Fly.io Sprites": "flyio",
  "Google Drive": "googledrive",
  "Together AI": "together",
  "Vercel AI Gateway": "vercel",
  "Vercel Sandbox": "vercel",
  "Vertex AI": "google_vertex",
};

/** The a13n-ui brand registry's icon for a product or provider named on the page. */
export function brandIcon(name: string) {
  const brand = brands[IDENTITIES[name] ?? name.toLowerCase()];
  if (!brand) throw new Error(`No brand icon for ${name}`);
  return brand.icon;
}
