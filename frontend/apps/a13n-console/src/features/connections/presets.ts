import type { Schema } from "../../shared/api";

export interface MCPPreset {
  id: string;
  name: string;
  description: string;
  endpoint: string;
  auth: Schema["MCPSource"]["auth_mode"];
  docs: string;
  logo: string;
  requirements: string;
  headerNames?: readonly string[];
}

export { mcpPresets } from "./presets-data";
