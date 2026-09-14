use crate::*;

/// Search operations bound to an immutable Workspace ID by Client::workspace().
/// Borrows the parent transport and shares its shutdown.
pub struct WorkspaceClient<'a> {
    client: &'a Client,
    scope: SearchScope,
}
impl<'a> WorkspaceClient<'a> {
    pub(crate) fn new(client: &'a Client, workspace_id: String) -> Self {
        Self {
            client,
            scope: SearchScope::Workspace(workspace_id),
        }
    }
    pub async fn search_providers(
        &self,
        options: &SearchListOptions,
    ) -> Result<Representation<Page<SearchProvider>>, Error> {
        self.client.search_providers(&self.scope, options).await
    }
    pub async fn search_provider(
        &self,
        provider_id: &str,
    ) -> Result<Representation<SearchProvider>, Error> {
        self.client.search_provider(&self.scope, provider_id).await
    }
    pub async fn create_search_provider(
        &self,
        request: &CreateSearchProviderRequest,
    ) -> Result<Representation<SearchProvider>, Error> {
        self.client
            .create_search_provider(&self.scope, request)
            .await
    }
    pub async fn update_search_provider(
        &self,
        provider_id: &str,
        etag: &str,
        request: &UpdateSearchProviderRequest,
    ) -> Result<Representation<SearchProvider>, Error> {
        self.client
            .update_search_provider(&self.scope, provider_id, etag, request)
            .await
    }
    pub async fn test_search_provider(
        &self,
        provider_id: &str,
    ) -> Result<Representation<SearchProviderTestResult>, Error> {
        self.client
            .test_search_provider(&self.scope, provider_id)
            .await
    }
    pub async fn search_provider_references(
        &self,
        provider_id: &str,
        options: &SearchListOptions,
    ) -> Result<Representation<Page<SearchProviderReference>>, Error> {
        self.client
            .search_provider_references(&self.scope, provider_id, options)
            .await
    }
}
