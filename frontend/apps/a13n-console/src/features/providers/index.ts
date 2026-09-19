/**
 * The provider scaffold every category composes: one table, one catalog-first
 * creation dialog, one connect step, one editor shell, one credential
 * expression. Category modules pass definitions, adapters, and save logic only.
 */
export {
  AddProviderDialog,
  ProviderCatalog,
  type ProviderDefinition,
} from "./add-provider-dialog";
export {
  providerCategories,
  providerCategory,
  type ProviderCategoryValue,
} from "./categories";
export { ProviderConnectFields } from "./connect-fields";
export { credentialDescription, credentialHint } from "./credential-hint";
export { CredentialsPill, type CredentialState } from "./credentials-pill";
export { EditProviderDialog } from "./edit-provider-dialog";
export { providerKeyUrls } from "./key-urls";
export { ManageProvidersLink } from "./manage-link";
export { providersPath } from "./navigation";
export { ProviderTable, type ProviderRow } from "./provider-table";
export { default as providerStyles } from "./providers.module.css";
export {
  hasFields,
  schemaProperties,
  splitConfigurationSchema,
} from "./schemas";
