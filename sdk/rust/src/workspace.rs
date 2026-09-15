use crate::*;

/// Search operations bound to an immutable Workspace ID by Client::workspace().
/// Borrows the parent transport and shares its shutdown.
pub struct WorkspaceClient<'a> {
    client: &'a Client,
    scope: WebProviderScope,
}
impl<'a> WorkspaceClient<'a> {
    pub(crate) fn new(client: &'a Client, workspace_id: String) -> Self {
        Self {
            client,
            scope: WebProviderScope::Workspace(workspace_id),
        }
    }
    pub async fn web_providers(
        &self,
        options: &WebProviderListOptions,
    ) -> Result<Representation<Page<WebProvider>>, Error> {
        self.client.web_providers(&self.scope, options).await
    }
    pub async fn web_provider(
        &self,
        provider_id: &str,
    ) -> Result<Representation<WebProvider>, Error> {
        self.client.web_provider(&self.scope, provider_id).await
    }
    pub async fn create_web_provider(
        &self,
        request: &CreateWebProviderRequest,
    ) -> Result<Representation<WebProvider>, Error> {
        self.client.create_web_provider(&self.scope, request).await
    }
    pub async fn update_web_provider(
        &self,
        provider_id: &str,
        etag: &str,
        request: &UpdateWebProviderRequest,
    ) -> Result<Representation<WebProvider>, Error> {
        self.client
            .update_web_provider(&self.scope, provider_id, etag, request)
            .await
    }
    pub async fn test_web_provider(
        &self,
        provider_id: &str,
    ) -> Result<Representation<WebProviderTestResult>, Error> {
        self.client
            .test_web_provider(&self.scope, provider_id)
            .await
    }
    pub async fn web_provider_references(
        &self,
        provider_id: &str,
        options: &WebProviderListOptions,
    ) -> Result<Representation<Page<WebProviderReference>>, Error> {
        self.client
            .web_provider_references(&self.scope, provider_id, options)
            .await
    }
}
