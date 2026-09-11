import type { Schema } from "../../shared/api";

export interface MCPPreset {
  id: string;
  name: string;
  description: string;
  endpoint: string;
  auth: Schema["MCPAuthMode"];
  docs: string;
  source?: string;
  logo?: string;
  notice?: string;
  unavailableReason?: string;
}

export { mcpPresets } from "./presets-data";
