import type { MCPServerId } from "a13n-mcp-directory";
import type { Schema } from "../../shared/api";

export interface MCPPreset {
  id: MCPServerId;
  name: string;
  description: string;
  endpoint: string;
  auth: Schema["MCPSource"]["auth_mode"];
  docs: string;
  requirements: string;
  headerNames?: readonly string[];
}

export { mcpPresets } from "./presets-data";
