import type { Schema } from "../../shared/api";

export interface MCPPreset {
  id: string;
  name: string;
  description: string;
  endpoint: string;
  auth: Schema["MCPAuthMode"];
  docs: string;
  logo: string;
  requirements: string;
  oauthClient?: "preregistered";
  headerNames?: readonly string[];
}

export { mcpPresets } from "./presets-data";
