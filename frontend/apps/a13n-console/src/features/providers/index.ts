/**
 * The provider scaffold every category composes: one table, one catalog-first
 * creation dialog, one editor shell, one credential expression. Category
 * modules pass definitions and adapters only.
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
export { CredentialsPill, type CredentialState } from "./credentials-pill";
export { EditProviderDialog } from "./edit-provider-dialog";
export { ManageProvidersLink } from "./manage-link";
export { providersPath } from "./navigation";
export { ProviderTable, type ProviderRow } from "./provider-table";
