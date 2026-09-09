export interface paths {
  "/api/v1/agent-revisions/{agent_revision_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Agent Revision */
    get: operations["get_agent_revision_api_v1_agent_revisions__agent_revision_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/api-keys/{key_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Key Metadata */
    get: operations["key_metadata_api_v1_api_keys__key_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/api-keys/{key_id}/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Key */
    post: operations["revoke_key_api_v1_api_keys__key_id__revoke_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Account */
    get: operations["get_account_api_v1_application_accounts__account_id__get"];
    put?: never;
    post?: never;
    /** Delete Account */
    delete: operations["delete_account_api_v1_application_accounts__account_id__delete"];
    options?: never;
    head?: never;
    /** Update Account */
    patch: operations["update_account_api_v1_application_accounts__account_id__patch"];
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/credentials": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Replace Account Credentials */
    put: operations["replace_account_credentials_api_v1_application_accounts__account_id__credentials_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/targets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Targets */
    get: operations["list_targets_api_v1_application_accounts__account_id__targets_get"];
    put?: never;
    /** Create */
    post: operations["create_api_v1_application_accounts__account_id__targets_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/targets/{target_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get */
    get: operations["get_api_v1_application_accounts__account_id__targets__target_id__get"];
    /** Replace */
    put: operations["replace_api_v1_application_accounts__account_id__targets__target_id__put"];
    post?: never;
    /** Delete */
    delete: operations["delete_api_v1_application_accounts__account_id__targets__target_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/{action}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Change Account Lifecycle */
    post: operations["change_account_lifecycle_api_v1_application_accounts__account_id___action__post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/assets/{asset_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Asset */
    get: operations["get_asset_api_v1_assets__asset_id__get"];
    put?: never;
    post?: never;
    /** Delete Asset */
    delete: operations["delete_asset_api_v1_assets__asset_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/assets/{asset_id}/content": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Asset Content */
    get: operations["get_asset_content_api_v1_assets__asset_id__content_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/configuration": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Auth Configuration */
    get: operations["auth_configuration_api_v1_auth_configuration_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/context": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Credential Context */
    get: operations["credential_context_api_v1_auth_context_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/csrf": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Browser Proof */
    get: operations["browser_proof_api_v1_auth_csrf_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/login": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Login */
    post: operations["login_api_v1_auth_login_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/logout": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Logout */
    post: operations["logout_api_v1_auth_logout_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/password-reset": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Request Password Reset */
    post: operations["request_password_reset_api_v1_auth_password_reset_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/password-reset/complete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Complete Password Reset */
    post: operations["complete_password_reset_api_v1_auth_password_reset_complete_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-connections/{connection_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Connector Connection */
    get: operations["get_connector_connection_api_v1_connector_connections__connection_id__get"];
    put?: never;
    post?: never;
    /** Delete Connector Connection */
    delete: operations["delete_connector_connection_api_v1_connector_connections__connection_id__delete"];
    options?: never;
    head?: never;
    /** Update Connector Connection */
    patch: operations["update_connector_connection_api_v1_connector_connections__connection_id__patch"];
    trace?: never;
  };
  "/api/v1/connector-connections/{connection_id}/reconnect": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reconnect Connector Connection */
    post: operations["reconnect_connector_connection_api_v1_connector_connections__connection_id__reconnect_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-connections/{connection_id}/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Connector Connection */
    post: operations["revoke_connector_connection_api_v1_connector_connections__connection_id__revoke_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-connections/{connection_id}/setup": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Start Connector Connection Setup */
    post: operations["start_connector_connection_setup_api_v1_connector_connections__connection_id__setup_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-connections/{connection_id}/{action}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Change Connector Connection Lifecycle */
    post: operations["change_connector_connection_lifecycle_api_v1_connector_connections__connection_id___action__post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-provider-types": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Connector Provider Types */
    get: operations["list_connector_provider_types_api_v1_connector_provider_types_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{connector_provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Connector Provider */
    get: operations["get_connector_provider_api_v1_connector_providers__connector_provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Connector Provider */
    patch: operations["update_connector_provider_api_v1_connector_providers__connector_provider_id__patch"];
    trace?: never;
  };
  "/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}/tools": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Preview Connector Tools */
    get: operations["preview_connector_tools_api_v1_connector_providers__connector_provider_id__connectors__connector_key__tools_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{connector_provider_id}/credentials": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Replace Connector Provider Credentials */
    post: operations["replace_connector_provider_credentials_api_v1_connector_providers__connector_provider_id__credentials_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{connector_provider_id}/discover-connectors": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discover Connectors */
    post: operations["discover_connectors_api_v1_connector_providers__connector_provider_id__discover_connectors_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{connector_provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Connector Provider */
    post: operations["test_connector_provider_api_v1_connector_providers__connector_provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{connector_provider_id}/{action}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Change Connector Provider Lifecycle */
    post: operations["change_connector_provider_lifecycle_api_v1_connector_providers__connector_provider_id___action__post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-setup/complete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Complete Connector Setup */
    post: operations["complete_connector_setup_api_v1_connector_setup_complete_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-commands/{command_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Command */
    get: operations["get_command_api_v1_environment_commands__command_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-provider-types": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Provider Types */
    get: operations["provider_types_api_v1_environment_provider_types_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-provider-types/{provider_type}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Provider Type */
    get: operations["get_provider_type_api_v1_environment_provider_types__provider_type__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Provider */
    patch: operations["update_provider_api_v1_environment_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/environment-providers/{provider_id}/credential": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Replace Credential */
    put: operations["replace_credential_api_v1_environment_providers__provider_id__credential_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-providers/{resource_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Provider */
    get: operations["get_provider_api_v1_environment_providers__resource_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-template-revisions/{revision_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Revision */
    get: operations["get_revision_api_v1_environment_template_revisions__revision_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-templates/{resource_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Template */
    get: operations["get_template_api_v1_environment_templates__resource_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-templates/{template_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Template */
    patch: operations["update_template_api_v1_environment_templates__template_id__patch"];
    trace?: never;
  };
  "/api/v1/environment-templates/{template_id}/revisions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Revisions */
    get: operations["list_revisions_api_v1_environment_templates__template_id__revisions_get"];
    put?: never;
    /** Create Revision */
    post: operations["create_revision_api_v1_environment_templates__template_id__revisions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/delete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Delete Environment */
    post: operations["delete_environment_api_v1_environments__environment_id__delete_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/stop": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Stop Environment */
    post: operations["stop_environment_api_v1_environments__environment_id__stop_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{resource_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Environment */
    get: operations["get_environment_api_v1_environments__resource_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/hook-subscriptions/{subscription_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Hook Subscription */
    get: operations["get_hook_subscription_api_v1_hook_subscriptions__subscription_id__get"];
    /** Update Hook Subscription */
    put: operations["update_hook_subscription_api_v1_hook_subscriptions__subscription_id__put"];
    post?: never;
    /** Delete Hook Subscription */
    delete: operations["delete_hook_subscription_api_v1_hook_subscriptions__subscription_id__delete"];
    options?: never;
    head?: never;
    /** Update Hook Subscription State */
    patch: operations["update_hook_subscription_state_api_v1_hook_subscriptions__subscription_id__patch"];
    trace?: never;
  };
  "/api/v1/hook-subscriptions/{subscription_id}/deliveries/{delivery_id}/redrive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Redrive Hook Delivery */
    post: operations["redrive_hook_delivery_api_v1_hook_subscriptions__subscription_id__deliveries__delivery_id__redrive_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/invitations/{invitation_id}/accept": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Accept Invitation */
    post: operations["accept_invitation_api_v1_invitations__invitation_id__accept_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/invitations/{invitation_id}/resend": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Resend Invitation */
    post: operations["resend_invitation_api_v1_invitations__invitation_id__resend_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/invitations/{invitation_id}/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Invitation */
    post: operations["revoke_invitation_api_v1_invitations__invitation_id__revoke_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/mcp-connections/{connection_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Mcp Connection */
    get: operations["get_mcp_connection_api_v1_mcp_connections__connection_id__get"];
    put?: never;
    post?: never;
    /** Delete Mcp Connection */
    delete: operations["delete_mcp_connection_api_v1_mcp_connections__connection_id__delete"];
    options?: never;
    head?: never;
    /** Update Mcp Connection */
    patch: operations["update_mcp_connection_api_v1_mcp_connections__connection_id__patch"];
    trace?: never;
  };
  "/api/v1/mcp-connections/{connection_id}/authorize": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Authorize Mcp Connection */
    post: operations["authorize_mcp_connection_api_v1_mcp_connections__connection_id__authorize_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/mcp-connections/{connection_id}/credentials": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Replace Mcp Credentials */
    post: operations["replace_mcp_credentials_api_v1_mcp_connections__connection_id__credentials_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/mcp-connections/{connection_id}/discover": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discover Mcp Tools */
    post: operations["discover_mcp_tools_api_v1_mcp_connections__connection_id__discover_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/mcp-connections/{connection_id}/reconnect": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reconnect Mcp Connection */
    post: operations["reconnect_mcp_connection_api_v1_mcp_connections__connection_id__reconnect_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/mcp-connections/{connection_id}/{action}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Change Mcp Connection Lifecycle */
    post: operations["change_mcp_connection_lifecycle_api_v1_mcp_connections__connection_id___action__post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/model-provider-types": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Model Provider Types */
    get: operations["list_model_provider_types_api_v1_model_provider_types_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/oauth/mcp/callback": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Mcp Oauth Callback */
    get: operations["mcp_oauth_callback_api_v1_oauth_mcp_callback_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/oauth/mcp/client-metadata.json": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Mcp Client Metadata */
    get: operations["mcp_client_metadata_api_v1_oauth_mcp_client_metadata_json_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organizations */
    get: operations["organizations_api_v1_organizations_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization */
    get: operations["organization_api_v1_organizations__organization__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Organization */
    patch: operations["update_organization_api_v1_organizations__organization__patch"];
    trace?: never;
  };
  "/api/v1/organizations/{organization}/connector-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization List Connector Providers */
    get: operations["organization_list_connector_providers_api_v1_organizations__organization__connector_providers_get"];
    put?: never;
    /** Organization Create Connector Provider */
    post: operations["organization_create_connector_provider_api_v1_organizations__organization__connector_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/environment-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization List Providers */
    get: operations["organization_list_providers_api_v1_organizations__organization__environment_providers_get"];
    put?: never;
    /** Organization Create Provider */
    post: operations["organization_create_provider_api_v1_organizations__organization__environment_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/environment-templates": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization List Templates */
    get: operations["organization_list_templates_api_v1_organizations__organization__environment_templates_get"];
    put?: never;
    /** Organization Create Template */
    post: operations["organization_create_template_api_v1_organizations__organization__environment_templates_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/icon": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Put Organization Icon */
    put: operations["put_organization_icon_api_v1_organizations__organization__icon_put"];
    post?: never;
    /** Delete Organization Icon */
    delete: operations["delete_organization_icon_api_v1_organizations__organization__icon_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/icon/{image_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Organization Icon */
    get: operations["get_organization_icon_api_v1_organizations__organization__icon__image_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/invitations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Invitations */
    get: operations["invitations_api_v1_organizations__organization__invitations_get"];
    put?: never;
    /** Invite */
    post: operations["invite_api_v1_organizations__organization__invitations_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/model-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization List Model Providers */
    get: operations["organization_list_model_providers_api_v1_organizations__organization__model_providers_get"];
    put?: never;
    /** Organization Create Model Provider */
    post: operations["organization_create_model_provider_api_v1_organizations__organization__model_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/model-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization Get Model Provider */
    get: operations["organization_get_model_provider_api_v1_organizations__organization__model_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Organization Update Model Provider */
    patch: operations["organization_update_model_provider_api_v1_organizations__organization__model_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/organizations/{organization}/model-providers/{provider_id}/describe-model": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Organization Describe Provider Model */
    post: operations["organization_describe_provider_model_api_v1_organizations__organization__model_providers__provider_id__describe_model_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/model-providers/{provider_id}/discover-models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Organization Discover Provider Models */
    post: operations["organization_discover_provider_models_api_v1_organizations__organization__model_providers__provider_id__discover_models_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/model-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Organization Test Model Provider */
    post: operations["organization_test_model_provider_api_v1_organizations__organization__model_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization List Models */
    get: operations["organization_list_models_api_v1_organizations__organization__models_get"];
    put?: never;
    /** Organization Create Model */
    post: operations["organization_create_model_api_v1_organizations__organization__models_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/models/{model_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization Get Model */
    get: operations["organization_get_model_api_v1_organizations__organization__models__model_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Organization Update Model */
    patch: operations["organization_update_model_api_v1_organizations__organization__models__model_id__patch"];
    trace?: never;
  };
  "/api/v1/organizations/{organization}/models/{model_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Organization Test Model */
    post: operations["organization_test_model_api_v1_organizations__organization__models__model_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/permissions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization Permissions */
    get: operations["organization_permissions_api_v1_organizations__organization__permissions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/role-bindings": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization Roles */
    get: operations["organization_roles_api_v1_organizations__organization__role_bindings_get"];
    put?: never;
    /** Create Organization Binding */
    post: operations["create_organization_binding_api_v1_organizations__organization__role_bindings_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/search-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organization Provider */
    get: operations["list_organization_provider_api_v1_organizations__organization__search_providers_get"];
    put?: never;
    /** Create Organization Provider */
    post: operations["create_organization_provider_api_v1_organizations__organization__search_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/search-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Organization Provider */
    get: operations["get_organization_provider_api_v1_organizations__organization__search_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Organization Provider */
    patch: operations["update_organization_provider_api_v1_organizations__organization__search_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/organizations/{organization}/search-providers/{provider_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** References Organization Provider */
    get: operations["references_organization_provider_api_v1_organizations__organization__search_providers__provider_id__references_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/search-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Organization Provider */
    post: operations["test_organization_provider_api_v1_organizations__organization__search_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/security-audit-events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization Security Events */
    get: operations["organization_security_events_api_v1_organizations__organization__security_audit_events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/users": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Users */
    get: operations["users_api_v1_organizations__organization__users_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/workspaces": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspaces */
    get: operations["workspaces_api_v1_organizations__organization__workspaces_get"];
    put?: never;
    /** Create Workspace */
    post: operations["create_workspace_api_v1_organizations__organization__workspaces_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/queued-submissions/{queued_submission_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Queued Submission */
    get: operations["get_queued_submission_api_v1_queued_submissions__queued_submission_id__get"];
    put?: never;
    post?: never;
    /** Delete Queued Submission */
    delete: operations["delete_queued_submission_api_v1_queued_submissions__queued_submission_id__delete"];
    options?: never;
    head?: never;
    /** Update Queued Submission */
    patch: operations["update_queued_submission_api_v1_queued_submissions__queued_submission_id__patch"];
    trace?: never;
  };
  "/api/v1/role-bindings/{binding_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Role Binding */
    get: operations["role_binding_api_v1_role_bindings__binding_id__get"];
    put?: never;
    post?: never;
    /** Remove Member */
    delete: operations["remove_member_api_v1_role_bindings__binding_id__delete"];
    options?: never;
    head?: never;
    /** Change Role */
    patch: operations["change_role_api_v1_role_bindings__binding_id__patch"];
    trace?: never;
  };
  "/api/v1/run-attempts/{run_attempt_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Run Attempt */
    get: operations["get_run_attempt_api_v1_run_attempts__run_attempt_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/run-attempts/{run_attempt_id}/events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Run Attempt Events */
    get: operations["list_run_attempt_events_api_v1_run_attempts__run_attempt_id__events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Run */
    get: operations["get_run_api_v1_runs__run_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/attempts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Run Attempts */
    get: operations["list_run_attempts_api_v1_runs__run_id__attempts_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Run Events */
    get: operations["list_run_events_api_v1_runs__run_id__events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/feedback": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Feedback Run */
    post: operations["feedback_run_api_v1_runs__run_id__feedback_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/fork": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Fork Run */
    post: operations["fork_run_api_v1_runs__run_id__fork_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/interrupt": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Interrupt Run */
    post: operations["interrupt_run_api_v1_runs__run_id__interrupt_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/items": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Run Items */
    get: operations["list_run_items_api_v1_runs__run_id__items_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/lineage": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Run Lineage */
    get: operations["get_run_lineage_api_v1_runs__run_id__lineage_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/pending-actions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Pending Actions */
    get: operations["list_pending_actions_api_v1_runs__run_id__pending_actions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/retry": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Retry Run */
    post: operations["retry_run_api_v1_runs__run_id__retry_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/steer": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Steer Run */
    post: operations["steer_run_api_v1_runs__run_id__steer_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/steers/{steer_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Run Steer */
    get: operations["get_run_steer_api_v1_runs__run_id__steers__steer_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/stream": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Stream Run */
    get: operations["stream_run_api_v1_runs__run_id__stream_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{source_run_id}/continue": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Continue From Run */
    post: operations["continue_from_run_api_v1_runs__source_run_id__continue_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/search-provider-types": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Types */
    get: operations["list_types_api_v1_search_provider_types_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/search-provider-types/{provider_type}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Type */
    get: operations["get_type_api_v1_search_provider_types__provider_type__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/service-accounts/{account_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Account */
    get: operations["account_api_v1_service_accounts__account_id__get"];
    put?: never;
    post?: never;
    /** Delete Account */
    delete: operations["delete_account_api_v1_service_accounts__account_id__delete"];
    options?: never;
    head?: never;
    /** Update Account */
    patch: operations["update_account_api_v1_service_accounts__account_id__patch"];
    trace?: never;
  };
  "/api/v1/service-accounts/{account_id}/api-keys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Account Keys */
    get: operations["account_keys_api_v1_service_accounts__account_id__api_keys_get"];
    put?: never;
    /** Create Account Key */
    post: operations["create_account_key_api_v1_service_accounts__account_id__api_keys_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/sessions/{session_id}/threads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Threads */
    get: operations["list_threads_api_v1_sessions__session_id__threads_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skill-revisions/{skill_revision_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Skill Revision */
    get: operations["get_skill_revision_api_v1_skill_revisions__skill_revision_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skill-revisions/{skill_revision_id}/content": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Skill Revision Content */
    get: operations["get_skill_revision_content_api_v1_skill_revisions__skill_revision_id__content_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skill-uploads/{upload_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Skill Upload */
    get: operations["get_skill_upload_api_v1_skill_uploads__upload_id__get"];
    put?: never;
    post?: never;
    /** Delete Skill Upload */
    delete: operations["delete_skill_upload_api_v1_skill_uploads__upload_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/{skill_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Skill */
    get: operations["get_skill_api_v1_skills__skill_id__get"];
    put?: never;
    post?: never;
    /** Delete Skill */
    delete: operations["delete_skill_api_v1_skills__skill_id__delete"];
    options?: never;
    head?: never;
    /** Update Skill */
    patch: operations["update_skill_api_v1_skills__skill_id__patch"];
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Skill References */
    get: operations["list_skill_references_api_v1_skills__skill_id__references_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/revisions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Skill Revisions */
    get: operations["list_skill_revisions_api_v1_skills__skill_id__revisions_get"];
    put?: never;
    /** Create Skill Revision */
    post: operations["create_skill_revision_api_v1_skills__skill_id__revisions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Thread */
    get: operations["get_thread_api_v1_threads__thread_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/queued-submissions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Queued Submissions */
    get: operations["list_queued_submissions_api_v1_threads__thread_id__queued_submissions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/queued-submissions/consume": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Consume Queued Submission */
    post: operations["consume_queued_submission_api_v1_threads__thread_id__queued_submissions_consume_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/queued-submissions/reorder": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reorder Queued Submissions */
    post: operations["reorder_queued_submissions_api_v1_threads__thread_id__queued_submissions_reorder_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/runs": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Thread Runs */
    get: operations["list_thread_runs_api_v1_threads__thread_id__runs_get"];
    put?: never;
    /** Submit Thread Run */
    post: operations["submit_thread_run_api_v1_threads__thread_id__runs_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Current User */
    get: operations["current_user_api_v1_users_me_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Profile */
    patch: operations["update_profile_api_v1_users_me_patch"];
    trace?: never;
  };
  "/api/v1/users/me/auth-sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Sessions */
    get: operations["sessions_api_v1_users_me_auth_sessions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/auth-sessions/{session_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Revoke Session */
    delete: operations["revoke_session_api_v1_users_me_auth_sessions__session_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/avatar": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Put Avatar */
    put: operations["put_avatar_api_v1_users_me_avatar_put"];
    post?: never;
    /** Delete Avatar */
    delete: operations["delete_avatar_api_v1_users_me_avatar_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/email-change": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Request Email Change */
    post: operations["request_email_change_api_v1_users_me_email_change_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/email-change/complete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Complete Email Change */
    post: operations["complete_email_change_api_v1_users_me_email_change_complete_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/password": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Change Password */
    post: operations["change_password_api_v1_users_me_password_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/security-activity": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Personal Security Activity */
    get: operations["personal_security_activity_api_v1_users_me_security_activity_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/{user_id}/avatar/{image_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Avatar */
    get: operations["get_avatar_api_v1_users__user_id__avatar__image_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspace */
    get: operations["workspace_api_v1_workspaces__workspace__get"];
    put?: never;
    post?: never;
    /** Delete Workspace */
    delete: operations["delete_workspace_api_v1_workspaces__workspace__delete"];
    options?: never;
    head?: never;
    /** Update Workspace */
    patch: operations["update_workspace_api_v1_workspaces__workspace__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Agents */
    get: operations["list_agents_api_v1_workspaces__workspace__agents_get"];
    put?: never;
    /** Create Agent */
    post: operations["create_agent_api_v1_workspaces__workspace__agents_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Agent */
    get: operations["get_agent_api_v1_workspaces__workspace__agents__agent__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Agent */
    patch: operations["update_agent_api_v1_workspaces__workspace__agents__agent__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}/duplicate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Duplicate Agent */
    post: operations["duplicate_agent_api_v1_workspaces__workspace__agents__agent__duplicate_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}/revisions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Agent Revisions */
    get: operations["list_agent_revisions_api_v1_workspaces__workspace__agents__agent__revisions_get"];
    put?: never;
    /** Create Agent Revision */
    post: operations["create_agent_revision_api_v1_workspaces__workspace__agents__agent__revisions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}/revisions/{revision_id}/restore": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Restore Agent Revision */
    post: operations["restore_agent_revision_api_v1_workspaces__workspace__agents__agent__revisions__revision_id__restore_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}/{action}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Change Agent Lifecycle */
    post: operations["change_agent_lifecycle_api_v1_workspaces__workspace__agents__agent___action__post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/api-keys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspace Member Keys */
    get: operations["workspace_member_keys_api_v1_workspaces__workspace__api_keys_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/application-account-provider-types": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Account Provider Types */
    get: operations["account_provider_types_api_v1_workspaces__workspace__application_account_provider_types_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/application-accounts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Accounts */
    get: operations["list_accounts_api_v1_workspaces__workspace__application_accounts_get"];
    put?: never;
    /** Create Account */
    post: operations["create_account_api_v1_workspaces__workspace__application_accounts_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/assets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Assets */
    get: operations["list_assets_api_v1_workspaces__workspace__assets_get"];
    put?: never;
    /** Upload Asset */
    post: operations["upload_asset_api_v1_workspaces__workspace__assets_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/connector-connections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Connector Connections */
    get: operations["list_connector_connections_api_v1_workspaces__workspace__connector_connections_get"];
    put?: never;
    /** Create Connector Connection */
    post: operations["create_connector_connection_api_v1_workspaces__workspace__connector_connections_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/connector-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Connector Providers */
    get: operations["list_connector_providers_api_v1_workspaces__workspace__connector_providers_get"];
    put?: never;
    /** Create Connector Provider */
    post: operations["create_connector_provider_api_v1_workspaces__workspace__connector_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/environment-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Providers */
    get: operations["list_providers_api_v1_workspaces__workspace__environment_providers_get"];
    put?: never;
    /** Create Provider */
    post: operations["create_provider_api_v1_workspaces__workspace__environment_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/environment-templates": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Templates */
    get: operations["list_templates_api_v1_workspaces__workspace__environment_templates_get"];
    put?: never;
    /** Create Template */
    post: operations["create_template_api_v1_workspaces__workspace__environment_templates_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/environments": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Environments */
    get: operations["list_environments_api_v1_workspaces__workspace__environments_get"];
    put?: never;
    /** Create Environment */
    post: operations["create_environment_api_v1_workspaces__workspace__environments_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Events */
    get: operations["list_workspace_events_api_v1_workspaces__workspace__events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/hook-subscriptions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Hook Subscriptions */
    get: operations["list_hook_subscriptions_api_v1_workspaces__workspace__hook_subscriptions_get"];
    put?: never;
    /** Create Hook Subscription */
    post: operations["create_hook_subscription_api_v1_workspaces__workspace__hook_subscriptions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/icon": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Put Workspace Icon */
    put: operations["put_workspace_icon_api_v1_workspaces__workspace__icon_put"];
    post?: never;
    /** Delete Workspace Icon */
    delete: operations["delete_workspace_icon_api_v1_workspaces__workspace__icon_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/icon/{image_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Workspace Icon */
    get: operations["get_workspace_icon_api_v1_workspaces__workspace__icon__image_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/invitations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspace Invitations */
    get: operations["workspace_invitations_api_v1_workspaces__workspace__invitations_get"];
    put?: never;
    /** Invite To Workspace */
    post: operations["invite_to_workspace_api_v1_workspaces__workspace__invitations_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/mcp-connections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Mcp Connections */
    get: operations["list_mcp_connections_api_v1_workspaces__workspace__mcp_connections_get"];
    put?: never;
    /** Create Mcp Connection */
    post: operations["create_mcp_connection_api_v1_workspaces__workspace__mcp_connections_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/members": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspace Members */
    get: operations["workspace_members_api_v1_workspaces__workspace__members_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/model-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Model Providers */
    get: operations["list_model_providers_api_v1_workspaces__workspace__model_providers_get"];
    put?: never;
    /** Create Model Provider */
    post: operations["create_model_provider_api_v1_workspaces__workspace__model_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/model-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Model Provider */
    get: operations["get_model_provider_api_v1_workspaces__workspace__model_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Model Provider */
    patch: operations["update_model_provider_api_v1_workspaces__workspace__model_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/model-providers/{provider_id}/describe-model": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Describe Provider Model */
    post: operations["describe_provider_model_api_v1_workspaces__workspace__model_providers__provider_id__describe_model_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/model-providers/{provider_id}/discover-models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discover Provider Models */
    post: operations["discover_provider_models_api_v1_workspaces__workspace__model_providers__provider_id__discover_models_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/model-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Model Provider */
    post: operations["test_model_provider_api_v1_workspaces__workspace__model_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Models */
    get: operations["list_models_api_v1_workspaces__workspace__models_get"];
    put?: never;
    /** Create Model */
    post: operations["create_model_api_v1_workspaces__workspace__models_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/models/{model_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Model */
    get: operations["get_model_api_v1_workspaces__workspace__models__model_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Model */
    patch: operations["update_model_api_v1_workspaces__workspace__models__model_id__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/models/{model_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Model */
    post: operations["test_model_api_v1_workspaces__workspace__models__model_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/permissions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspace Permissions */
    get: operations["workspace_permissions_api_v1_workspaces__workspace__permissions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/personal-api-keys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Personal Keys */
    get: operations["personal_keys_api_v1_workspaces__workspace__personal_api_keys_get"];
    put?: never;
    /** Create Personal Key */
    post: operations["create_personal_key_api_v1_workspaces__workspace__personal_api_keys_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/role-bindings": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspace Roles */
    get: operations["workspace_roles_api_v1_workspaces__workspace__role_bindings_get"];
    put?: never;
    /** Add Workspace Member */
    post: operations["add_workspace_member_api_v1_workspaces__workspace__role_bindings_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/runs": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Runs */
    get: operations["list_workspace_runs_api_v1_workspaces__workspace__runs_get"];
    put?: never;
    /** Start Run */
    post: operations["start_run_api_v1_workspaces__workspace__runs_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/search-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Provider */
    get: operations["list_workspace_provider_api_v1_workspaces__workspace__search_providers_get"];
    put?: never;
    /** Create Workspace Provider */
    post: operations["create_workspace_provider_api_v1_workspaces__workspace__search_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/search-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Workspace Provider */
    get: operations["get_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Workspace Provider */
    patch: operations["update_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/search-providers/{provider_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** References Workspace Provider */
    get: operations["references_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__references_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/search-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Workspace Provider */
    post: operations["test_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/security-audit-events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Workspace Security Events */
    get: operations["workspace_security_events_api_v1_workspaces__workspace__security_audit_events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/service-accounts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Accounts */
    get: operations["accounts_api_v1_workspaces__workspace__service_accounts_get"];
    put?: never;
    /** Create Account */
    post: operations["create_account_api_v1_workspaces__workspace__service_accounts_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Sessions */
    get: operations["list_sessions_api_v1_workspaces__workspace__sessions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/skill-uploads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Stage Skill Upload */
    post: operations["stage_skill_upload_api_v1_workspaces__workspace__skill_uploads_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/skills": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Skills */
    get: operations["list_skills_api_v1_workspaces__workspace__skills_get"];
    put?: never;
    /** Create Skill */
    post: operations["create_skill_api_v1_workspaces__workspace__skills_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/threads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Create Thread */
    post: operations["create_thread_api_v1_workspaces__workspace__threads_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/traces": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Traces */
    get: operations["list_traces_api_v1_workspaces__workspace__traces_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/traces/{trace_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Trace */
    get: operations["get_trace_api_v1_workspaces__workspace__traces__trace_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
}
export type webhooks = Record<string, never>;
export interface components {
  schemas: {
    /** A2ASkillProjection */
    A2ASkillProjection: {
      /** Description */
      description?: string | null;
      /** Id */
      id: string;
      /** Name */
      name: string;
    };
    /** AcceptInvitationRequest */
    AcceptInvitationRequest: {
      /** Name */
      name?: string | null;
      /**
       * Password
       * Format: password
       */
      password: string;
      /**
       * Token
       * Format: password
       */
      token: string;
    };
    /** Account */
    Account: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Credential Configured */
      credential_configured: boolean;
      /** Credential Generation */
      credential_generation: number;
      /** Default Agent Id */
      default_agent_id?: string | null;
      /** Execution Service Account Id */
      execution_service_account_id?: string | null;
      /** Id */
      id: string;
      input_batching?: components["schemas"]["InputBatchingPolicy"] | null;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Provider Config */
      provider_config: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Provider Config Version */
      provider_config_version: string;
      /** Provider Key */
      provider_key: string;
      /** Provider Policy */
      provider_policy?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Receive Enabled
       * @default false
       */
      receive_enabled?: boolean;
      status: components["schemas"]["AccountStatus"];
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** AccountCollection */
    AccountCollection: {
      /** Items */
      items: components["schemas"]["Account"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** AccountCommandRequest */
    AccountCommandRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /** AccountProviderDefinition */
    AccountProviderDefinition: {
      /** Config Version */
      config_version: string;
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential Schema */
      credential_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Provider Key */
      provider_key: string;
      /** Reception Policy Schema */
      reception_policy_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Target Kinds */
      target_kinds: ("conversation" | "repository")[];
    };
    /** AccountProviderDefinitionCollection */
    AccountProviderDefinitionCollection: {
      /** Items */
      items: components["schemas"]["AccountProviderDefinition"][];
    };
    /**
     * AccountStatus
     * @enum {string}
     */
    AccountStatus: "active" | "disabled";
    /** AccountTarget */
    AccountTarget: {
      /** Account Id */
      account_id: string;
      /** Agent Id */
      agent_id?: string | null;
      config_override?: components["schemas"]["InputOverride"] | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** External Target Id */
      external_target_id: string;
      /** Id */
      id: string;
      input_batching?: components["schemas"]["InputBatchingPolicy"] | null;
      /** Organization Id */
      organization_id: string;
      /** Provider Policy */
      provider_policy?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Receive Enabled
       * @default true
       */
      receive_enabled?: boolean;
      /**
       * Target Kind
       * @enum {string}
       */
      target_kind: "conversation" | "repository";
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /**
     * ActivityMessage
     * @description An activity progress message emitted between chat messages.
     */
    ActivityMessage: {
      /** Activitytype */
      activityType: string;
      /** Content */
      content: {
        [key: string]: unknown;
      };
      /** Id */
      id: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      role: "activity";
    } & {
      [key: string]: unknown;
    };
    ActorRef:
      | components["schemas"]["PrincipalRef"]
      | components["schemas"]["SystemActorRef"];
    /** Agent */
    Agent: {
      /** Archived At */
      archived_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["ActorRef"];
      /** Current Revision Id */
      current_revision_id: string;
      /** Default Environment Template Id */
      default_environment_template_id?: string | null;
      /** Description */
      description: string | null;
      /** Duplicated From Agent Id */
      duplicated_from_agent_id: string | null;
      /** Duplicated From Revision Id */
      duplicated_from_revision_id: string | null;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      source: components["schemas"]["AgentSource"];
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      updated_by: components["schemas"]["ActorRef"];
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** AgentCollection */
    AgentCollection: {
      /** Items */
      items: components["schemas"]["Agent"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AgentConfig */
    "AgentConfig-Input": {
      asset_publication?:
        components["schemas"]["AssetPublicationConfig"] | null;
      /**
       * Client Tools
       * @default []
       */
      client_tools?: components["schemas"]["ClientToolDefinition"][];
      /**
       * Connector Tools
       * @default []
       */
      connector_tools?: components["schemas"]["ConnectorConnectionToolSelection"][];
      input_adapter: components["schemas"]["InputAdapterConfig"];
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      /**
       * Mcp Tools
       * @default []
       */
      mcp_tools?: components["schemas"]["MCPConnectionToolSelection"][];
      model: components["schemas"]["AgentModel"];
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /**
       * Plugins
       * @default []
       */
      plugins?: components["schemas"]["PluginSelection"][];
      protocol: components["schemas"]["ProtocolConfig"];
      retries?: components["schemas"]["RetryConfig"] | null;
      search?: components["schemas"]["SearchSelection"] | null;
      /**
       * Secret Requirements
       * @default []
       */
      secret_requirements?: components["schemas"]["SecretRequirement"][];
      /**
       * Skills
       * @default []
       */
      skills?: components["schemas"]["SkillSelection"][];
      /**
       * Subagent Mode
       * @default inline
       * @enum {string}
       */
      subagent_mode?: "inline" | "async";
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentSelection-Input"];
      };
    };
    /** AgentConfig */
    "AgentConfig-Output": {
      asset_publication?:
        components["schemas"]["AssetPublicationConfig"] | null;
      /**
       * Client Tools
       * @default []
       */
      client_tools?: components["schemas"]["ClientToolDefinition"][];
      /**
       * Connector Tools
       * @default []
       */
      connector_tools?: components["schemas"]["ConnectorConnectionToolSelection"][];
      input_adapter: components["schemas"]["InputAdapterConfig"];
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      /**
       * Mcp Tools
       * @default []
       */
      mcp_tools?: components["schemas"]["MCPConnectionToolSelection"][];
      model: components["schemas"]["AgentModel"];
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /**
       * Plugins
       * @default []
       */
      plugins?: components["schemas"]["PluginSelection"][];
      protocol: components["schemas"]["ProtocolConfig"];
      retries?: components["schemas"]["RetryConfig"] | null;
      search?: components["schemas"]["SearchSelection"] | null;
      /**
       * Secret Requirements
       * @default []
       */
      secret_requirements?: components["schemas"]["SecretRequirement"][];
      /**
       * Skills
       * @default []
       */
      skills?: components["schemas"]["SkillSelection"][];
      /**
       * Subagent Mode
       * @default inline
       * @enum {string}
       */
      subagent_mode?: "inline" | "async";
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentSelection-Output"];
      };
    };
    /**
     * AgentInput
     * @description Submitted or retained versioned ordinary Agent input.
     */
    AgentInput: {
      /**
       * Content
       * @default []
       */
      content?: (
        | components["schemas"]["TextContent"]
        | components["schemas"]["BinaryContent"]
      )[];
      /**
       * Schema Version
       * @enum {string}
       */
      schema_version: "1" | "2";
      /**
       * Secret Bindings
       * @default []
       */
      secret_bindings?: components["schemas"]["AgentSecretBinding"][];
      structured_content?: components["schemas"]["JsonValue"] | null;
    };
    /** AgentModel */
    AgentModel: {
      characteristics?: components["schemas"]["HarnessModelCharacteristics"];
      /** Model Key */
      model_key: string;
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** AgentRevision */
    AgentRevision: {
      /** Agent Id */
      agent_id: string;
      config: components["schemas"]["AgentConfig-Output"];
      /** Config Digest */
      config_digest: string;
      /**
       * Connector Tools
       * @default []
       */
      connector_tools?: components["schemas"]["ConnectorConnectionToolSelection"][];
      /** Content Digest */
      content_digest: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["ActorRef"];
      /** Id */
      id: string;
      /**
       * Mcp Tools
       * @default []
       */
      mcp_tools?: components["schemas"]["MCPConnectionToolSelection"][];
      /** Organization Id */
      organization_id: string;
      resolved_model: components["schemas"]["ResolvedAgentModel"];
      /** Resolved Skills */
      resolved_skills: components["schemas"]["ResolvedSkillBinding"][];
      /** Resolved Subagents */
      resolved_subagents: components["schemas"]["ResolvedSubagentEdge"][];
      /** Source Revision Id */
      source_revision_id: string | null;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** AgentRevisionCollection */
    AgentRevisionCollection: {
      /** Items */
      items: components["schemas"]["AgentRevision"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AgentRevisionCreateResult */
    AgentRevisionCreateResult: {
      agent: components["schemas"]["Agent"];
      revision: components["schemas"]["AgentRevision"];
    };
    /** AgentRunOverride */
    "AgentRunOverride-Input": {
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connector Tools */
      connector_tools?:
        components["schemas"]["ConnectorConnectionToolSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      /** Mcp Tools */
      mcp_tools?: components["schemas"]["MCPConnectionToolSelection"][] | null;
      model?: components["schemas"]["ModelOverride"] | null;
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /** Plugins */
      plugins?: components["schemas"]["PluginSelection"][] | null;
      retries?: components["schemas"]["RetryOverride"] | null;
      search?: components["schemas"]["SearchSelection"] | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentOverride-Input"] | null;
      } | null;
    };
    /** AgentRunOverride */
    "AgentRunOverride-Output": {
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connector Tools */
      connector_tools?:
        components["schemas"]["ConnectorConnectionToolSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      /** Mcp Tools */
      mcp_tools?: components["schemas"]["MCPConnectionToolSelection"][] | null;
      model?: components["schemas"]["ModelOverride"] | null;
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /** Plugins */
      plugins?: components["schemas"]["PluginSelection"][] | null;
      retries?: components["schemas"]["RetryOverride"] | null;
      search?: components["schemas"]["SearchSelection"] | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentOverride-Output"] | null;
      } | null;
    };
    /**
     * AgentSecretBinding
     * @description Bind one declared requirement to a non-secret, owner-scoped lookup intent.
     */
    AgentSecretBinding: {
      /** Credential */
      credential:
        | components["schemas"]["WorkspaceSecretCredential"]
        | components["schemas"]["InvokingUserSecretCredential"];
      /** Key */
      key: string;
    };
    /**
     * AgentSource
     * @enum {string}
     */
    AgentSource: "builtin" | "custom";
    /** ApiKey */
    ApiKey: {
      /** Boundary Id */
      boundary_id: string;
      /** Boundary Type */
      boundary_type: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Expires At */
      expires_at: string | null;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Principal Id */
      principal_id: string;
      /** Principal Type */
      principal_type: string;
      /** Revoked At */
      revoked_at: string | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
    };
    /** ApprovePendingResolution */
    ApprovePendingResolution: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "approve";
      /** Call Id */
      call_id: string;
    };
    /**
     * Asset
     * @description One exact immutable binary publication and its lifecycle marker.
     */
    Asset: {
      /** Content Sha256 */
      content_sha256: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Deleted At */
      deleted_at: string | null;
      /** Filename */
      filename: string;
      /** Id */
      id: string;
      /** Media Type */
      media_type: string;
      /** Organization Id */
      organization_id: string;
      /** Size Bytes */
      size_bytes: number;
      /** Source */
      source:
        | components["schemas"]["UploadedAssetSource"]
        | components["schemas"]["RunOutputAssetSource"];
      /** Workspace Id */
      workspace_id: string;
    };
    /** AssetBinarySource */
    AssetBinarySource: {
      /** Asset Id */
      asset_id: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "asset";
    };
    /** AssetCollection */
    AssetCollection: {
      /** Items */
      items: components["schemas"]["Asset"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AssetPublicationConfig */
    AssetPublicationConfig: {
      /**
       * Enabled
       * @default true
       * @constant
       */
      enabled?: true;
    };
    /**
     * AssetSourceKind
     * @enum {string}
     */
    AssetSourceKind: "upload" | "run_output";
    /**
     * AssistantMessage
     * @description An assistant message.
     */
    AssistantMessage: {
      /** Content */
      content?: string | null;
      /** Encryptedvalue */
      encryptedValue?: string | null;
      /** Id */
      id: string;
      /** Name */
      name?: string | null;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      role: "assistant";
      /** Toolcalls */
      toolCalls?: components["schemas"]["ToolCall"][] | null;
    } & {
      [key: string]: unknown;
    };
    /**
     * AudioInputContent
     * @description An audio input content fragment.
     */
    AudioInputContent: {
      /** Metadata */
      metadata?: unknown | null;
      /** Source */
      source:
        | components["schemas"]["InputContentDataSource"]
        | components["schemas"]["InputContentUrlSource"];
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "audio";
    } & {
      [key: string]: unknown;
    };
    /** AuthConfiguration */
    AuthConfiguration: {
      /** Email Delivery */
      email_delivery: boolean;
    };
    /** AuthSession */
    AuthSession: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Id */
      id: string;
      /** Revoked At */
      revoked_at: string | null;
      /** User Id */
      user_id: string;
    };
    /** BinaryContent */
    BinaryContent: {
      /** @default auto */
      delivery?: components["schemas"]["BinaryContentDelivery"];
      /** Filename */
      filename?: string | null;
      /** Media Type */
      media_type?: string | null;
      /** Source */
      source:
        | components["schemas"]["UrlBinarySource"]
        | components["schemas"]["PathBinarySource"]
        | components["schemas"]["AssetBinarySource"];
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "binary";
    };
    /**
     * BinaryContentDelivery
     * @enum {string}
     */
    BinaryContentDelivery:
      "auto" | "model_content" | "model_url" | "environment_path";
    /**
     * BinaryInputContent
     * @description A deprecated binary payload reference in a multimodal user message.
     */
    BinaryInputContent: {
      /** Data */
      data?: string | null;
      /** Filename */
      filename?: string | null;
      /** Id */
      id?: string | null;
      /** Mimetype */
      mimeType: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "binary";
      /** Url */
      url?: string | null;
    } & {
      [key: string]: unknown;
    };
    /** ChangePasswordRequest */
    ChangePasswordRequest: {
      /**
       * Current Password
       * Format: password
       */
      current_password: string;
      /**
       * Password
       * Format: password
       */
      password: string;
    };
    /** ChangeRoleRequest */
    ChangeRoleRequest: {
      /**
       * Role
       * @enum {string}
       */
      role: "member" | "viewer" | "runner" | "builder" | "admin";
    };
    /** ChildEnvironmentPolicy */
    ChildEnvironmentPolicy: {
      /**
       * Mode
       * @default none
       * @enum {string}
       */
      mode?: "none" | "shared" | "dedicated";
      /** Template Revision Id */
      template_revision_id?: string | null;
    };
    /**
     * ClientToolDefinition
     * @description Portable model guidance for one externally executed client tool.
     */
    ClientToolDefinition: {
      /** Description */
      description: string;
      /** Instruction */
      instruction?: string | null;
      /** Metadata */
      metadata?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Name */
      name: string;
      /** Parameters Json Schema */
      parameters_json_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** ClientToolPolicy */
    ClientToolPolicy: {
      /** Name */
      name: string;
      /**
       * Required
       * @default false
       */
      required?: boolean;
    };
    /** Collection[EnvironmentProvider] */
    Collection_EnvironmentProvider_: {
      /** Items */
      items: components["schemas"]["EnvironmentProvider"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection[EnvironmentTemplateRevision] */
    Collection_EnvironmentTemplateRevision_: {
      /** Items */
      items: components["schemas"]["EnvironmentTemplateRevision"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection[EnvironmentTemplate] */
    Collection_EnvironmentTemplate_: {
      /** Items */
      items: components["schemas"]["EnvironmentTemplate"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection[Environment] */
    Collection_Environment_: {
      /** Items */
      items: components["schemas"]["Environment"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection[dict] */
    Collection_dict_: {
      /** Items */
      items: {
        [key: string]: unknown;
      }[];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** CompleteConnectorSetupRequest */
    CompleteConnectorSetupRequest: {
      /** Attempt Id */
      attempt_id: string;
      /** Browser Nonce */
      browser_nonce: string;
      /** Session Uri */
      session_uri: string;
    };
    /** CompleteEmailChangeRequest */
    CompleteEmailChangeRequest: {
      /**
       * Token
       * Format: password
       */
      token: string;
    };
    /** CompletePasswordResetRequest */
    CompletePasswordResetRequest: {
      /**
       * Password
       * Format: password
       */
      password: string;
      /**
       * Token
       * Format: password
       */
      token: string;
    };
    /** CompletePendingResolution */
    CompletePendingResolution: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "complete";
      /** Call Id */
      call_id: string;
      result: components["schemas"]["JsonValue"];
    };
    /** ConnectionCleanupReceipt */
    ConnectionCleanupReceipt: {
      /** Connection Id */
      connection_id: string;
      /**
       * Local Status
       * @enum {string}
       */
      local_status: "disabled" | "deleted";
      /**
       * Remote Status
       * @enum {string}
       */
      remote_status: "not_required" | "succeeded" | "failed" | "unknown";
    };
    /** Connector */
    Connector: {
      /** Authentication Methods */
      authentication_methods: string[];
      /** Connector Provider Id */
      connector_provider_id: string;
      /** Description */
      description?: string | null;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /** Setup Schema */
      setup_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** ConnectorCollection */
    ConnectorCollection: {
      /** Items */
      items: components["schemas"]["Connector"][];
      /** Next Cursor */
      next_cursor?: null;
    };
    /** ConnectorConnection */
    ConnectorConnection: {
      /** Connector Key */
      connector_key: string;
      /** Connector Provider Id */
      connector_provider_id: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Safe Metadata */
      safe_metadata: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      status: components["schemas"]["ConnectorConnectionStatus"];
      status_reason:
        components["schemas"]["ConnectorConnectionStatusReason"] | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** ConnectorConnectionCollection */
    ConnectorConnectionCollection: {
      /** Items */
      items: components["schemas"]["ConnectorConnection"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** ConnectorConnectionCommandRequest */
    ConnectorConnectionCommandRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /**
     * ConnectorConnectionStatus
     * @enum {string}
     */
    ConnectorConnectionStatus:
      "pending" | "ready" | "action_required" | "disabled";
    /**
     * ConnectorConnectionStatusReason
     * @enum {string}
     */
    ConnectorConnectionStatusReason:
      "reauthorization_required" | "incompatible";
    /** ConnectorConnectionToolSelection */
    ConnectorConnectionToolSelection: {
      /** Connector Connection Id */
      connector_connection_id: string;
      /**
       * Defer Loading
       * @default false
       */
      defer_loading?: boolean;
      /** Tools */
      tools?: string[] | null;
    };
    /** ConnectorProvider */
    ConnectorProvider: {
      /** Configuration */
      configuration: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Credential Configured */
      credential_configured: boolean;
      /** Credential Generation */
      credential_generation: number;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      status: components["schemas"]["ConnectorProviderStatus"];
      /** Type */
      type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** ConnectorProviderCollection */
    ConnectorProviderCollection: {
      /** Items */
      items: components["schemas"]["ConnectorProvider"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** ConnectorProviderCommandRequest */
    ConnectorProviderCommandRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /** ConnectorProviderDefinition */
    ConnectorProviderDefinition: {
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential Schema */
      credential_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Display Name */
      display_name: string;
      /** Type */
      type: string;
    };
    /** ConnectorProviderDefinitionCollection */
    ConnectorProviderDefinitionCollection: {
      /** Items */
      items: components["schemas"]["ConnectorProviderDefinition"][];
      /** Next Cursor */
      next_cursor?: null;
    };
    /**
     * ConnectorProviderStatus
     * @enum {string}
     */
    ConnectorProviderStatus: "active" | "disabled";
    /** ConnectorProviderTestResult */
    ConnectorProviderTestResult: {
      /** Connector Provider Id */
      connector_provider_id: string;
      /** Connector Provider Version */
      connector_provider_version: number;
      /**
       * Status
       * @default succeeded
       * @constant
       */
      status?: "succeeded";
      /**
       * Tested At
       * Format: date-time
       */
      tested_at: string;
      /** Verified Access */
      verified_access: ("catalog_read" | "account_read")[];
    };
    /** ConnectorSetupCompletion */
    ConnectorSetupCompletion: {
      /** Return Path */
      return_path: string;
    };
    /** ConnectorSetupLaunch */
    ConnectorSetupLaunch: {
      /** Attempt Id */
      attempt_id: string;
      connection: components["schemas"]["ConnectorConnection"];
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Redirect Url */
      redirect_url?: string | null;
      /**
       * Requires Browser Callback
       * @default false
       */
      requires_browser_callback?: boolean;
      /**
       * Status
       * @enum {string}
       */
      status: "pending" | "completed" | "failed" | "expired";
    };
    /** ConnectorTool */
    ConnectorTool: {
      /** Annotations */
      annotations?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Description */
      description: string;
      /** Input Schema */
      input_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Key */
      key: string;
      /** Output Schema */
      output_schema?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Provider Version */
      provider_version: string;
    };
    /** ConnectorToolPage */
    ConnectorToolPage: {
      /** Items */
      items: components["schemas"]["ConnectorTool"][];
      /** Next Cursor */
      next_cursor?: string | null;
      /** Provider Version */
      provider_version: string;
    };
    /** ConsumeQueuedSubmissionRequest */
    ConsumeQueuedSubmissionRequest: {
      /** Expected Queue Version */
      expected_queue_version: number;
      /** Expected Thread Version */
      expected_thread_version: number;
    };
    /**
     * Context
     * @description Additional context for the agent.
     */
    Context: {
      /** Description */
      description: string;
      /** Value */
      value: string;
    } & {
      [key: string]: unknown;
    };
    /** ContinueRunRequest */
    ContinueRunRequest: {
      /** Agent Id */
      agent_id?: string | null;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      config_override?: components["schemas"]["AgentRunOverride-Input"] | null;
      environment?: components["schemas"]["EnvironmentSelection"] | null;
      /** Expected Current Revision Id */
      expected_current_revision_id?: string | null;
      /** Expected Thread Version */
      expected_thread_version: number;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
      input: components["schemas"]["AgentInput"];
    };
    /** CreateAccountRequest */
    CreateAccountRequest: {
      /** Credentials */
      credentials: {
        [key: string]: string;
      };
      /** Default Agent Id */
      default_agent_id?: string | null;
      /** Execution Service Account Id */
      execution_service_account_id?: string | null;
      input_batching?: components["schemas"]["InputBatchingPolicy"] | null;
      /** Name */
      name: string;
      /** Provider Config */
      provider_config: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Provider Config Version */
      provider_config_version: string;
      /** Provider Key */
      provider_key: string;
      /** Provider Policy */
      provider_policy?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Receive Enabled
       * @default false
       */
      receive_enabled?: boolean;
    };
    /** CreateAgentRequest */
    CreateAgentRequest: {
      config: components["schemas"]["AgentConfig-Input"];
      /** Default Environment Template Id */
      default_environment_template_id?: string | null;
      /** Description */
      description?: string | null;
      /** Key */
      key?: string | null;
      /** Name */
      name: string;
    };
    /** CreateAgentRevisionRequest */
    CreateAgentRevisionRequest: {
      config: components["schemas"]["AgentConfig-Input"];
      /** Expected Version */
      expected_version: number;
    };
    /** CreateConnectorConnectionRequest */
    CreateConnectorConnectionRequest: {
      /** Connector Key */
      connector_key: string;
      /** Connector Provider Id */
      connector_provider_id: string;
      /** Name */
      name: string;
    };
    /** CreateConnectorProviderRequest */
    CreateConnectorProviderRequest: {
      /** Configuration */
      configuration: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credentials */
      credentials: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Type */
      type: string;
    };
    /** CreateHookSubscriptionRequest */
    CreateHookSubscriptionRequest: {
      /** Hook Names */
      hook_names: string[];
      /** Run Id */
      run_id?: string | null;
      /** Session Id */
      session_id?: string | null;
      /** Thread Id */
      thread_id?: string | null;
      webhook: components["schemas"]["WebhookDestinationConfig"];
    };
    /** CreateInvitationRequest */
    CreateInvitationRequest: {
      /**
       * Email
       * Format: email
       */
      email: string;
      /** Grants */
      grants: components["schemas"]["Grant"][];
    };
    /** CreateKeyRequest */
    CreateKeyRequest: {
      /** Expires At */
      expires_at?: string | null;
      /** Name */
      name: string;
    };
    /** CreateMCPConnectionRequest */
    CreateMCPConnectionRequest: {
      auth_mode: components["schemas"]["MCPAuthMode"];
      /** Endpoint Url */
      endpoint_url: string;
      /** Name */
      name: string;
      /**
       * Static Header Names
       * @default []
       */
      static_header_names?: string[];
    };
    /** CreateModelProviderRequest */
    CreateModelProviderRequest: {
      /** Configuration */
      configuration?: {
        [key: string]: unknown;
      };
      /** Credential */
      credential?: string | null;
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Name */
      name: string;
      /** Type */
      type: string;
    };
    /** CreateModelRequest */
    CreateModelRequest: {
      /** Description */
      description?: string | null;
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Key */
      key: string;
      /** Model Api */
      model_api: string;
      /** Name */
      name: string;
      /** Provider Id */
      provider_id: string;
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Upstream Model */
      upstream_model: string;
    };
    /** CreateProviderRequest */
    CreateProviderRequest: {
      /** Configuration */
      configuration?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential */
      credential?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Name */
      name: string;
      /** Type */
      type: string;
    };
    /** CreateSearchProviderRequest */
    CreateSearchProviderRequest: {
      configuration?: components["schemas"]["SearchConfiguration"];
      /**
       * Credential
       * Format: password
       */
      credential: string;
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Name */
      name: string;
      /**
       * Type
       * @enum {string}
       */
      type: "brave" | "exa";
    };
    /** CreateServiceAccountRequest */
    CreateServiceAccountRequest: {
      /** Description */
      description?: string | null;
      /** Name */
      name: string;
      /**
       * Role
       * @enum {string}
       */
      role: "viewer" | "runner" | "builder";
    };
    /** CreateSkillRequest */
    CreateSkillRequest: {
      /** Name */
      name?: string | null;
      /** Source */
      source:
        | components["schemas"]["ZipUploadSkillSource"]
        | components["schemas"]["GitHubRevisionSource"];
    };
    /** CreateSkillRevisionRequest */
    CreateSkillRevisionRequest: {
      /** Expected Version */
      expected_version: number;
      /** Source */
      source:
        | components["schemas"]["ZipUploadSkillSource"]
        | components["schemas"]["GitHubRevisionSource"];
    };
    /** CreateTemplateRequest */
    CreateTemplateRequest: {
      /** @default full */
      access?: components["schemas"]["EnvironmentAccess"];
      /** Configuration */
      configuration: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Configuration Schema Version
       * @default 1
       */
      configuration_schema_version?: string;
      /** Description */
      description?: string | null;
      /** Name */
      name: string;
      /**
       * Preparation
       * @default on_run
       * @enum {string}
       */
      preparation?: "on_run" | "on_use";
      /** Provider Id */
      provider_id: string;
      retention: components["schemas"]["RetentionPolicy"];
    };
    /** CreateTemplateRevisionRequest */
    CreateTemplateRevisionRequest: {
      /** @default full */
      access?: components["schemas"]["EnvironmentAccess"];
      /** Configuration */
      configuration: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Configuration Schema Version
       * @default 1
       */
      configuration_schema_version?: string;
      /** Expected Version */
      expected_version: number;
      /**
       * Preparation
       * @default on_run
       * @enum {string}
       */
      preparation?: "on_run" | "on_use";
      /** Provider Id */
      provider_id: string;
      retention: components["schemas"]["RetentionPolicy"];
    };
    /** CreateThreadRequest */
    CreateThreadRequest: {
      /** Agent Id */
      agent_id?: string | null;
      environment?: components["schemas"]["EnvironmentSelection"] | null;
      /** Session Id */
      session_id?: string | null;
    };
    /** CreateWorkspaceRequest */
    CreateWorkspaceRequest: {
      /** Key */
      key?: string | null;
      /** Name */
      name: string;
    };
    /** CreatedKey */
    CreatedKey: {
      /** Bearer */
      bearer: string;
      key: components["schemas"]["ApiKey"];
    };
    /**
     * CredentialContext
     * @description The authenticated credential boundary, independent of resource grants.
     */
    CredentialContext: {
      /** Organization Id */
      organization_id: string | null;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** DelegationContextPolicy */
    DelegationContextPolicy: {
      /**
       * History
       * @default none
       * @enum {string}
       */
      history?: "none" | "summary" | "selected";
      /**
       * Include Task
       * @default true
       */
      include_task?: boolean;
      /**
       * Task State
       * @default shared
       * @enum {string}
       */
      task_state?: "shared" | "isolated";
    };
    /** DeleteQueuedSubmissionRequest */
    DeleteQueuedSubmissionRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /** DescribeModelRequest */
    DescribeModelRequest: {
      /** Model Api */
      model_api?: string | null;
      /** Upstream Model */
      upstream_model: string;
    };
    /**
     * DeveloperMessage
     * @description A developer message.
     */
    DeveloperMessage: {
      /** Content */
      content: string;
      /** Encryptedvalue */
      encryptedValue?: string | null;
      /** Id */
      id: string;
      /** Name */
      name?: string | null;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      role: "developer";
    } & {
      [key: string]: unknown;
    };
    /**
     * DocumentInputContent
     * @description A document input content fragment.
     */
    DocumentInputContent: {
      /** Metadata */
      metadata?: unknown | null;
      /** Source */
      source:
        | components["schemas"]["InputContentDataSource"]
        | components["schemas"]["InputContentUrlSource"];
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "document";
    } & {
      [key: string]: unknown;
    };
    /** DuplicateAgentRequest */
    DuplicateAgentRequest: {
      /** Description */
      description?: string | null;
      /** Expected Version */
      expected_version: number;
      /** Key */
      key?: string | null;
      /** Name */
      name: string;
    };
    /** EmailChangeRequest */
    EmailChangeRequest: {
      /**
       * Current Password
       * Format: password
       */
      current_password: string;
      /**
       * Email
       * Format: email
       */
      email: string;
    };
    /** Environment */
    Environment: {
      access: components["schemas"]["EnvironmentAccess"];
      /**
       * Condition Since
       * Format: date-time
       */
      condition_since: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Generation */
      generation: number;
      /** Id */
      id: string;
      /** Organization Id */
      organization_id: string;
      /**
       * Ownership
       * @enum {string}
       */
      ownership: "managed" | "external";
      /** Provider Id */
      provider_id: string;
      /**
       * Retention Condition
       * @enum {string}
       */
      retention_condition: "active" | "idle";
      status: components["schemas"]["EnvironmentStatus"];
      /** Template Revision Id */
      template_revision_id: string | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /**
     * EnvironmentAccess
     * @enum {string}
     */
    EnvironmentAccess: "read_only" | "read_write" | "full";
    /** EnvironmentCommand */
    EnvironmentCommand: {
      /**
       * Action
       * @enum {string}
       */
      action: "stop" | "delete";
      /** Completed At */
      completed_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Environment Id */
      environment_id: string;
      /** Id */
      id: string;
      /**
       * Status
       * @enum {string}
       */
      status: "pending" | "completed" | "failed";
    };
    /** EnvironmentProvider */
    EnvironmentProvider: {
      /** Configuration */
      configuration: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Type */
      type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Workspace Id */
      workspace_id: string | null;
    };
    EnvironmentSelection:
      | components["schemas"]["ExistingEnvironmentSelection"]
      | components["schemas"]["NewEnvironmentSelection"];
    /**
     * EnvironmentState
     * @description Provider-owned portable semantic soft reference for one target.
     */
    EnvironmentState: {
      /** Provider Key */
      provider_key: string;
      state: components["schemas"]["JsonValue"];
      /** State Version */
      state_version: string;
    };
    /**
     * EnvironmentStatus
     * @enum {string}
     */
    EnvironmentStatus:
      "unprepared" | "running" | "stopped" | "deleted" | "unavailable";
    /** EnvironmentTemplate */
    EnvironmentTemplate: {
      /** Archived At */
      archived_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Revision Id */
      current_revision_id: string;
      /** Description */
      description: string | null;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** EnvironmentTemplateRevision */
    EnvironmentTemplateRevision: {
      /** @default full */
      access?: components["schemas"]["EnvironmentAccess"];
      /** Configuration */
      configuration: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Configuration Schema Version
       * @default 1
       */
      configuration_schema_version?: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Id */
      id: string;
      /** Organization Id */
      organization_id: string;
      /**
       * Preparation
       * @default on_run
       * @enum {string}
       */
      preparation?: "on_run" | "on_use";
      /** Provider Id */
      provider_id: string;
      retention: components["schemas"]["RetentionPolicy"];
      /** Template Id */
      template_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** ExistingEnvironmentSelection */
    ExistingEnvironmentSelection: {
      /** Environment Id */
      environment_id: string;
    };
    /** ExpectedVersion */
    ExpectedVersion: {
      /** Expected Version */
      expected_version: number;
    };
    /** ExtendedAgentCardPolicy */
    ExtendedAgentCardPolicy: {
      /**
       * Enabled
       * @default false
       */
      enabled?: boolean;
    };
    /** ForkRunRequest */
    ForkRunRequest: {
      /** Agent Id */
      agent_id?: string | null;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      config_override?: components["schemas"]["AgentRunOverride-Input"] | null;
      environment?: components["schemas"]["EnvironmentSelection"] | null;
      /** Expected Current Revision Id */
      expected_current_revision_id?: string | null;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
      input: components["schemas"]["AgentInput"];
    };
    /**
     * FunctionCall
     * @description Name and arguments of a function call.
     */
    FunctionCall: {
      /** Arguments */
      arguments: string;
      /** Name */
      name: string;
    } & {
      [key: string]: unknown;
    };
    /** GitHubRevisionSource */
    GitHubRevisionSource: {
      /** Credential Secret Id */
      credential_secret_id?: string | null;
      /** Expected Commit Sha */
      expected_commit_sha?: string | null;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "github";
      /** Ref */
      ref?: string | null;
      /** Repository Url */
      repository_url: string;
      /**
       * Subdirectory
       * @default
       */
      subdirectory?: string;
    };
    /** GitHubSkillImportProvenance */
    GitHubSkillImportProvenance: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "github";
      /** Repository Url */
      repository_url: string;
      /** Requested Ref */
      requested_ref?: string | null;
      /** Resolved Commit Sha */
      resolved_commit_sha: string;
      /** Subdirectory */
      subdirectory: string;
    };
    /** Grant */
    Grant: {
      /** Resource Id */
      resource_id: string;
      /**
       * Resource Type
       * @enum {string}
       */
      resource_type: "organization" | "workspace";
      /**
       * Role Key
       * @enum {string}
       */
      role_key: "member" | "viewer" | "runner" | "builder" | "admin";
    };
    /** HTTPValidationError */
    HTTPValidationError: {
      /** Detail */
      detail?: components["schemas"]["ValidationError"][];
    };
    /**
     * HarnessModelCharacteristics
     * @description Resolved Harness characteristics of the active Agent model.
     */
    HarnessModelCharacteristics: {
      /** Capabilities */
      capabilities?: components["schemas"]["ModelCapability"][];
      /**
       * Compact Threshold
       * @default 0.9
       */
      compact_threshold?: number;
      /** Context Window */
      context_window?: number | null;
      /**
       * Proactive Context Management Threshold
       * @default 0.65
       */
      proactive_context_management_threshold?: number | null;
    };
    /** HookSubscription */
    HookSubscription: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      current_revision: components["schemas"]["HookSubscriptionRevision"];
      /** Current Revision Id */
      current_revision_id: string;
      /** Deleted At */
      deleted_at?: string | null;
      /** Enabled */
      enabled: boolean;
      /** Expired At */
      expired_at?: string | null;
      /** Id */
      id: string;
      /** Inline Run Id */
      inline_run_id?: string | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      updated_by: components["schemas"]["PrincipalRef"];
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** HookSubscriptionCollection */
    HookSubscriptionCollection: {
      /** Items */
      items: components["schemas"]["HookSubscription"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** HookSubscriptionRevision */
    HookSubscriptionRevision: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Hook Names */
      hook_names: string[];
      /** Hook Subscription Id */
      hook_subscription_id: string;
      /** Id */
      id: string;
      /** Run Id */
      run_id?: string | null;
      /** Session Id */
      session_id?: string | null;
      /** Thread Id */
      thread_id?: string | null;
      /** Version */
      version: number;
      webhook: components["schemas"]["WebhookDestinationConfig"];
    };
    /** HostedAguiCancelReceipt */
    HostedAguiCancelReceipt: {
      /** Runid */
      runId: string;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /**
       * Status
       * @default cancelled
       * @constant
       */
      status?: "cancelled";
      /** Threadid */
      threadId: string;
    };
    /** HostedAguiCancelRequest */
    HostedAguiCancelRequest: {
      /** Runid */
      runId: string;
      /** Threadid */
      threadId: string;
    };
    /**
     * ImageInputContent
     * @description An image input content fragment.
     */
    ImageInputContent: {
      /** Metadata */
      metadata?: unknown | null;
      /** Source */
      source:
        | components["schemas"]["InputContentDataSource"]
        | components["schemas"]["InputContentUrlSource"];
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "image";
    } & {
      [key: string]: unknown;
    };
    /** InlineHookSubscriptionInput */
    InlineHookSubscriptionInput: {
      /** Hook Names */
      hook_names: string[];
      webhook: components["schemas"]["WebhookDestinationConfig"];
    };
    /** InputAdapterConfig */
    InputAdapterConfig: {
      /** Adapter Key */
      adapter_key: string;
      /** Config */
      config?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** InputBatchingPolicy */
    InputBatchingPolicy: {
      /** Max Batch Events */
      max_batch_events: number;
      /** Min Interval Ms */
      min_interval_ms: number;
    };
    /**
     * InputContentDataSource
     * @description Inline base64-encoded source.
     */
    InputContentDataSource: {
      /** Mimetype */
      mimeType: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "data";
      /** Value */
      value: string;
    } & {
      [key: string]: unknown;
    };
    /**
     * InputContentUrlSource
     * @description URL-referenced source.
     */
    InputContentUrlSource: {
      /** Mimetype */
      mimeType?: string | null;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "url";
      /** Value */
      value: string;
    } & {
      [key: string]: unknown;
    };
    /** InputOverride */
    InputOverride: {
      /** Connector Tools */
      connector_tools?:
        components["schemas"]["ConnectorConnectionToolSelection"][] | null;
      /** Mcp Tools */
      mcp_tools?: components["schemas"]["MCPConnectionToolSelection"][] | null;
      model?: components["schemas"]["ModelOverride"] | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
    };
    /** InterruptReceipt */
    InterruptReceipt: {
      /**
       * Interrupted At
       * Format: date-time
       */
      interrupted_at: string;
      /** Run Id */
      run_id: string;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /**
       * Status
       * @default cancelled
       * @constant
       */
      status?: "cancelled";
    };
    /** InterruptRequest */
    InterruptRequest: {
      /** Expected Run Version */
      expected_run_version: number;
      /** Expected Thread Version */
      expected_thread_version: number;
    };
    /** Invitation */
    Invitation: {
      /** Accepted At */
      accepted_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By User Id */
      created_by_user_id: string | null;
      /** Email */
      email: string;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Grants */
      grants: components["schemas"]["Grant"][];
      /** Id */
      id: string;
      /** Organization Id */
      organization_id: string;
      /** Revoked At */
      revoked_at: string | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Verification Mode */
      verification_mode: string;
      /** Version */
      version: number;
    };
    /** InvitationDelivery */
    InvitationDelivery: {
      /**
       * Delivery
       * @enum {string}
       */
      delivery: "sent" | "failed" | "manual";
      invitation: components["schemas"]["Invitation"];
      /** Invitation Url */
      invitation_url?: string | null;
    };
    /** InviteWorkspaceRequest */
    InviteWorkspaceRequest: {
      /**
       * Email
       * Format: email
       */
      email: string;
      /**
       * Role
       * @enum {string}
       */
      role: "viewer" | "runner" | "builder" | "admin";
    };
    /** InvokingUserSecretCredential */
    InvokingUserSecretCredential: {
      /** Secret Key */
      secret_key: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      source: "invoking_user_secret";
    };
    /** ItemCollection */
    ItemCollection: {
      /** Items */
      items: components["schemas"]["ItemResource"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ItemResource */
    ItemResource: {
      content: components["schemas"]["JsonValue"];
      /** First Stream Id */
      first_stream_id: string;
      /** Id */
      id: string;
      /** Kind */
      kind: string;
      /** Last Stream Id */
      last_stream_id: string;
      /** Parent Item Id */
      parent_item_id: string | null;
      /** State */
      state: string;
    };
    JsonValue: unknown;
    /**
     * LifecycleEntityType
     * @enum {string}
     */
    LifecycleEntityType: "run" | "run_attempt";
    /** LifecycleEvent */
    LifecycleEvent: {
      /** Actor Id */
      actor_id?: string | null;
      /** Actor Type */
      actor_type: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Entity Id */
      entity_id: string;
      entity_type: components["schemas"]["LifecycleEntityType"];
      /** Entity Version */
      entity_version: number;
      /** Event Type */
      event_type: string;
      /** Id */
      id: string;
      /** Mutation Id */
      mutation_id: string;
      /**
       * Occurred At
       * Format: date-time
       */
      occurred_at: string;
      /** Organization Id */
      organization_id: string;
      /** Payload */
      payload: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Projected At */
      projected_at?: string | null;
      /** Projection Attempts */
      projection_attempts: number;
      projection_error?: components["schemas"]["SafeFailure"] | null;
      /** Projection Lease Expires At */
      projection_lease_expires_at?: string | null;
      /** Projection Lease Owner */
      projection_lease_owner?: string | null;
      /** Projection Next Attempt At */
      projection_next_attempt_at?: string | null;
      projection_state: components["schemas"]["LifecycleProjectionState"];
      /** Resource Seq */
      resource_seq: number;
      /** Run Attempt Id */
      run_attempt_id?: string | null;
      /** Run Id */
      run_id: string;
      /** Schema Version */
      schema_version: string;
      /** Seq */
      seq: number;
      /** Session Id */
      session_id?: string | null;
      /** Thread Id */
      thread_id?: string | null;
    };
    /**
     * LifecycleProjectionState
     * @enum {string}
     */
    LifecycleProjectionState:
      "pending" | "projecting" | "retry_wait" | "projected" | "abandoned";
    /** LoginRequest */
    LoginRequest: {
      /**
       * Email
       * Format: email
       */
      email: string;
      /**
       * Password
       * Format: password
       */
      password: string;
    };
    /** LoginResult */
    LoginResult: {
      /** Csrf Token */
      csrf_token: string;
      session: components["schemas"]["AuthSession"];
      user: components["schemas"]["User"];
    };
    /**
     * MCPAuthMode
     * @enum {string}
     */
    MCPAuthMode: "none" | "bearer" | "oauth" | "static_headers";
    /** MCPAuthorizationLaunch */
    MCPAuthorizationLaunch: {
      /** Authorization Url */
      authorization_url: string;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Id */
      id: string;
      /**
       * Status
       * @default pending
       * @constant
       */
      status?: "pending";
    };
    /** MCPClientMetadata */
    MCPClientMetadata: {
      /** Client Id */
      client_id: string;
      /** Client Name */
      client_name: string;
      /**
       * Grant Types
       * @default [
       *       "authorization_code"
       *     ]
       */
      grant_types?: string[];
      /** Redirect Uris */
      redirect_uris: string[];
      /**
       * Response Types
       * @default [
       *       "code"
       *     ]
       */
      response_types?: string[];
      /**
       * Token Endpoint Auth Method
       * @default none
       */
      token_endpoint_auth_method?: string;
    };
    /** MCPConnection */
    MCPConnection: {
      auth_mode: components["schemas"]["MCPAuthMode"];
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Credential Configured */
      credential_configured: boolean;
      /** Credential Generation */
      credential_generation: number;
      /** Endpoint Url */
      endpoint_url: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Static Header Names */
      static_header_names: string[];
      status: components["schemas"]["MCPConnectionStatus"];
      status_reason: components["schemas"]["MCPConnectionStatusReason"] | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** MCPConnectionCollection */
    MCPConnectionCollection: {
      /** Items */
      items: components["schemas"]["MCPConnection"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** MCPConnectionCommandRequest */
    MCPConnectionCommandRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /**
     * MCPConnectionStatus
     * @enum {string}
     */
    MCPConnectionStatus: "pending" | "ready" | "action_required" | "disabled";
    /**
     * MCPConnectionStatusReason
     * @enum {string}
     */
    MCPConnectionStatusReason: "reauthorization_required" | "incompatible";
    /** MCPConnectionToolSelection */
    MCPConnectionToolSelection: {
      /**
       * Defer Loading
       * @default false
       */
      defer_loading?: boolean;
      /** Mcp Connection Id */
      mcp_connection_id: string;
      /** Tools */
      tools?: string[] | null;
    };
    /** MCPTool */
    MCPTool: {
      /** Annotations */
      annotations?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Description
       * @default
       */
      description?: string;
      /** Input Schema */
      input_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Name */
      name: string;
      /** Output Schema */
      output_schema?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
    };
    /** MCPToolCollection */
    MCPToolCollection: {
      /** Items */
      items: components["schemas"]["MCPTool"][];
    };
    /** Model */
    Model: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Description */
      description: string | null;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Key */
      key: string;
      /** Model Api */
      model_api: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Provider Id */
      provider_id: string;
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      updated_by: components["schemas"]["PrincipalRef"];
      /** Upstream Model */
      upstream_model: string;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** ModelCandidate */
    ModelCandidate: {
      /** Display Name */
      display_name?: string | null;
      limits?: components["schemas"]["ModelLimits"];
      /** Parameter Support */
      parameter_support?: {
        [key: string]: "supported" | "unsupported" | "unknown";
      };
      profile?: components["schemas"]["ModelProfile"];
      /** Suggested Model Api */
      suggested_model_api: string;
      /** Suggested Settings */
      suggested_settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Upstream Model */
      upstream_model: string;
    };
    /**
     * ModelCapability
     * @description Harness-owned capabilities of the active Agent model.
     * @enum {string}
     */
    ModelCapability:
      "image_understanding" | "video_understanding" | "audio_understanding";
    /** ModelCollection */
    ModelCollection: {
      /** Items */
      items: components["schemas"]["Model"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ModelConnectionTestResult */
    ModelConnectionTestResult: {
      /** Code */
      code: string;
      /** Elapsed Ms */
      elapsed_ms: number;
      /**
       * May Consume Quota Or Incur Cost
       * @default true
       * @constant
       */
      may_consume_quota_or_incur_cost?: true;
      /** Message */
      message: string;
      /** Success */
      success: boolean;
    };
    /** ModelDescription */
    ModelDescription: {
      /** Display Name */
      display_name?: string | null;
      limits?: components["schemas"]["ModelLimits"];
      /** Parameter Support */
      parameter_support?: {
        [key: string]: "supported" | "unsupported" | "unknown";
      };
      profile?: components["schemas"]["ModelProfile"];
      /** Settings Schema */
      settings_schema: {
        [key: string]: unknown;
      };
      /** Suggested Model Api */
      suggested_model_api: string;
      /** Suggested Settings */
      suggested_settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Upstream Model */
      upstream_model: string;
    };
    /** ModelDiscovery */
    ModelDiscovery: {
      /** Items */
      items: components["schemas"]["ModelCandidate"][];
      /** Settings Schemas */
      settings_schemas: {
        [key: string]: {
          [key: string]: unknown;
        };
      };
    };
    /** ModelLimits */
    ModelLimits: {
      /** Context Window Tokens */
      context_window_tokens?: number | null;
      /** Max Output Tokens */
      max_output_tokens?: number | null;
    };
    /** ModelOverride */
    ModelOverride: {
      characteristics?:
        components["schemas"]["HarnessModelCharacteristics"] | null;
      /** Model Key */
      model_key?: string | null;
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
    };
    /**
     * ModelProfile
     * @description Read-only Provider capability information returned by discovery and description.
     */
    ModelProfile: {
      /** Input Modalities */
      input_modalities?: ("text" | "image" | "audio" | "video")[] | null;
      /** Supports Audio Input */
      supports_audio_input?: boolean | null;
      /** Supports Image Output */
      supports_image_output?: boolean | null;
      /** Supports Json Object Output */
      supports_json_object_output?: boolean | null;
      /** Supports Json Schema Output */
      supports_json_schema_output?: boolean | null;
      /** Supports Thinking */
      supports_thinking?: boolean | null;
      /** Supports Tools */
      supports_tools?: boolean | null;
      /** Thinking Always Enabled */
      thinking_always_enabled?: boolean | null;
    };
    /** ModelProvider */
    ModelProvider: {
      /** Configuration */
      configuration: {
        [key: string]: unknown;
      };
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Type */
      type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      updated_by: components["schemas"]["PrincipalRef"];
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** ModelProviderCollection */
    ModelProviderCollection: {
      /** Items */
      items: components["schemas"]["ModelProvider"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ModelProviderDefinition */
    ModelProviderDefinition: {
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: unknown;
      };
      /** Credential Schema */
      credential_schema: {
        [key: string]: unknown;
      };
      /** Default Model Api */
      default_model_api: string;
      /** Display Name */
      display_name: string;
      /** Supported Model Apis */
      supported_model_apis: string[];
      /** Supports Model Discovery */
      supports_model_discovery: boolean;
      /** Type */
      type: string;
    };
    /** ModelProviderDefinitionCollection */
    ModelProviderDefinitionCollection: {
      /** Items */
      items: components["schemas"]["ModelProviderDefinition"][];
      /** Next Cursor */
      next_cursor?: null;
    };
    /**
     * ModelTestRequest
     * @description Model tests use the saved API and settings without a request selector.
     */
    ModelTestRequest: Record<string, never>;
    /** NewEnvironmentSelection */
    NewEnvironmentSelection: {
      /** Template Id */
      template_id: string;
      /** Version */
      version?: number | null;
    };
    /** NotificationSubscription */
    NotificationSubscription: {
      /** Resource Id */
      resource_id: string;
      /**
       * Scope
       * @enum {string}
       */
      scope: "thread" | "workspace";
      /** Subscription Id */
      subscription_id: string;
      /** Topics */
      topics: (
        | "thread.updated"
        | "run.updated"
        | "pending_action.updated"
        | "session.updated"
      )[];
    };
    /** Observation */
    Observation: {
      /** Cost Usd */
      cost_usd: string | null;
      /** Duration Ms */
      duration_ms: number | null;
      /** Ended At */
      ended_at: string | null;
      /** Id */
      id: string;
      input: components["schemas"]["JsonValue"] | null;
      /** Metadata */
      metadata: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Model */
      model: string | null;
      /** Name */
      name: string;
      output: components["schemas"]["JsonValue"] | null;
      /** Parent Id */
      parent_id: string | null;
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
      /**
       * Status
       * @enum {string}
       */
      status: "unset" | "ok" | "error";
      /**
       * Type
       * @enum {string}
       */
      type: "span" | "generation" | "event" | "unknown";
      /** Usage */
      usage: {
        [key: string]: number;
      } | null;
    };
    /** Organization */
    Organization: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Id */
      id: string;
      /** Image Url */
      readonly image_url: string | null;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
    };
    /** OrganizationPermissions */
    OrganizationPermissions: {
      /** Organization Admin */
      organization_admin: boolean;
    };
    /** OutputSpec */
    OutputSpec: {
      /** Description */
      description?: string | null;
      /** Name */
      name?: string | null;
      /** Resources */
      resources?: {
        [key: string]: {
          [key: string]: components["schemas"]["JsonValue"];
        };
      };
      /** Schema */
      schema?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Variants */
      variants?: components["schemas"]["OutputVariant"][] | null;
    };
    /** OutputVariant */
    OutputVariant: {
      /** Description */
      description?: string | null;
      /** Name */
      name: string;
      /** Resources */
      resources?: {
        [key: string]: {
          [key: string]: components["schemas"]["JsonValue"];
        };
      };
      /** Schema */
      schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** Page[ApiKey] */
    Page_ApiKey_: {
      /** Items */
      items: components["schemas"]["ApiKey"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[AuthSession] */
    Page_AuthSession_: {
      /** Items */
      items: components["schemas"]["AuthSession"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[Invitation] */
    Page_Invitation_: {
      /** Items */
      items: components["schemas"]["Invitation"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[Organization] */
    Page_Organization_: {
      /** Items */
      items: components["schemas"]["Organization"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[RoleBinding] */
    Page_RoleBinding_: {
      /** Items */
      items: components["schemas"]["RoleBinding"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[SecurityEvent] */
    Page_SecurityEvent_: {
      /** Items */
      items: components["schemas"]["SecurityEvent"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[ServiceAccount] */
    Page_ServiceAccount_: {
      /** Items */
      items: components["schemas"]["ServiceAccount"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[User] */
    Page_User_: {
      /** Items */
      items: components["schemas"]["User"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page[Workspace] */
    Page_Workspace_: {
      /** Items */
      items: components["schemas"]["Workspace"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** PasswordResetRequest */
    PasswordResetRequest: {
      /**
       * Email
       * Format: email
       */
      email: string;
    };
    /** PathBinarySource */
    PathBinarySource: {
      /** Environment Binding */
      environment_binding: string;
      /** Path */
      path: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "path";
    };
    /** PendingActionCollection */
    PendingActionCollection: {
      /** Items */
      items: components["schemas"]["PendingActionResource"][];
    };
    /** PendingActionResource */
    PendingActionResource: {
      /** Call Id */
      call_id: string;
      /** Kind */
      kind: string;
      presentation: components["schemas"]["JsonValue"] | null;
      /** Provider Type */
      provider_type: string | null;
      /** Tool Name */
      tool_name: string | null;
    };
    /** Permissions */
    Permissions: {
      /** Actions */
      actions: string[];
      /** Organization Admin */
      organization_admin: boolean;
    };
    /** PluginSelection */
    PluginSelection: {
      /** Config */
      config?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Instance Name */
      instance_name: string;
      /** Plugin Key */
      plugin_key: string;
    };
    /** PrincipalRef */
    PrincipalRef: {
      /** Principal Id */
      principal_id: string;
      principal_type: components["schemas"]["PrincipalType"];
    };
    /**
     * PrincipalType
     * @enum {string}
     */
    PrincipalType: "user" | "service_account";
    /** ProtocolConfig */
    ProtocolConfig: {
      /**
       * A2A Skills
       * @default []
       */
      a2a_skills?: components["schemas"]["A2ASkillProjection"][];
      /**
       * Client Tools
       * @default []
       */
      client_tools?: components["schemas"]["ClientToolPolicy"][];
      /** Context Schema */
      context_schema?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Event Visibility
       * @default []
       */
      event_visibility?: string[];
      extended_agent_card?:
        components["schemas"]["ExtendedAgentCardPolicy"] | null;
      /** Input Data Schema */
      input_data_schema?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      limits?: components["schemas"]["ProtocolLimits"];
      /**
       * Output Modes
       * @default [
       *       "text"
       *     ]
       */
      output_modes?: string[];
      /** Public Description */
      public_description?: string | null;
      /** Public Name */
      public_name: string;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /** State Schema */
      state_schema?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
    };
    /** ProtocolLimits */
    ProtocolLimits: {
      /**
       * Max Event Bytes
       * @default 1048576
       */
      max_event_bytes?: number;
      /**
       * Max Input Bytes
       * @default 1048576
       */
      max_input_bytes?: number;
      /**
       * Max Output Bytes
       * @default 16777216
       */
      max_output_bytes?: number;
    };
    /** QueuedSubmission */
    QueuedSubmission: {
      authority_principal: components["schemas"]["PrincipalRef"];
      /** Consumed At */
      consumed_at?: string | null;
      /** Consumed Run Id */
      consumed_run_id?: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Failed At */
      failed_at?: string | null;
      failure?: components["schemas"]["QueuedSubmissionFailure"] | null;
      /** Position */
      position?: number | null;
      /** Queued Submission Id */
      queued_submission_id: string;
      state: components["schemas"]["QueuedSubmissionState"];
      submission: components["schemas"]["ThreadRunSubmissionIntent-Output"];
      /** Submission Digest Sha256 */
      submission_digest_sha256: string;
      /** Thread Id */
      thread_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /** QueuedSubmissionCollection */
    QueuedSubmissionCollection: {
      /** Items */
      items: components["schemas"]["QueuedSubmission"][];
    };
    /** QueuedSubmissionConsumptionReceipt */
    QueuedSubmissionConsumptionReceipt: {
      /**
       * Outcome
       * @enum {string}
       */
      outcome: "run_accepted" | "submission_failed";
      /** Queue Version */
      queue_version: number;
      queued_submission: components["schemas"]["QueuedSubmission"];
      run?: components["schemas"]["RunAcceptanceReceipt"] | null;
    };
    /** QueuedSubmissionFailure */
    QueuedSubmissionFailure: {
      /** Code */
      code: string;
      /** Message */
      message: string;
    };
    /** QueuedSubmissionMutationReceipt */
    QueuedSubmissionMutationReceipt: {
      /** Queue Version */
      queue_version: number;
      queued_submission: components["schemas"]["QueuedSubmission"];
    };
    /**
     * QueuedSubmissionState
     * @enum {string}
     */
    QueuedSubmissionState: "queued" | "consumed" | "failed";
    /**
     * ReasoningMessage
     * @description A reasoning message containing the agent's internal reasoning process.
     */
    ReasoningMessage: {
      /** Content */
      content: string;
      /** Encryptedvalue */
      encryptedValue?: string | null;
      /** Id */
      id: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      role: "reasoning";
    } & {
      [key: string]: unknown;
    };
    /** ReconnectConnectorConnectionRequest */
    ReconnectConnectorConnectionRequest: {
      /** Browser Nonce */
      browser_nonce?: string | null;
      /** Expected Version */
      expected_version: number;
      /** Return Path */
      return_path: string;
      /** Setup */
      setup: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** RegisterEnvironmentRequest */
    RegisterEnvironmentRequest: {
      /** @default full */
      access?: components["schemas"]["EnvironmentAccess"];
      /** Configuration */
      configuration: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Configuration Schema Version
       * @default 1
       */
      configuration_schema_version?: string;
      /** Provider Id */
      provider_id: string;
      state?: components["schemas"]["EnvironmentState"] | null;
    };
    /** RejectPendingResolution */
    RejectPendingResolution: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "reject";
      /** Call Id */
      call_id: string;
    };
    /** ReorderQueuedSubmissionsRequest */
    ReorderQueuedSubmissionsRequest: {
      /** Expected Queue Version */
      expected_queue_version: number;
      /** Queued Submission Ids */
      queued_submission_ids: string[];
    };
    /** ReplaceAccountCredentialsRequest */
    ReplaceAccountCredentialsRequest: {
      /** Credentials */
      credentials: {
        [key: string]: string;
      };
      /** Expected Version */
      expected_version: number;
    };
    /** ReplaceConnectorProviderCredentialsRequest */
    ReplaceConnectorProviderCredentialsRequest: {
      /** Credentials */
      credentials: {
        [key: string]: string;
      };
      /** Expected Version */
      expected_version: number;
    };
    /** ReplaceCredentialRequest */
    ReplaceCredentialRequest: {
      /** Credential */
      credential: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
    };
    /** ReplaceMCPCredentialsRequest */
    ReplaceMCPCredentialsRequest: {
      /** Bearer */
      bearer?: string | null;
      /** Expected Version */
      expected_version: number;
      /** Static Headers */
      static_headers?: {
        [key: string]: string;
      } | null;
    };
    /** ReplaceTargetRequest */
    ReplaceTargetRequest: {
      /** Agent Id */
      agent_id?: string | null;
      config_override?: components["schemas"]["InputOverride"] | null;
      /** Expected Version */
      expected_version: number;
      /** External Target Id */
      external_target_id: string;
      input_batching?: components["schemas"]["InputBatchingPolicy"] | null;
      /** Provider Policy */
      provider_policy?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Receive Enabled
       * @default true
       */
      receive_enabled?: boolean;
      /**
       * Target Kind
       * @enum {string}
       */
      target_kind: "conversation" | "repository";
    };
    /** ResolvedAgentModel */
    ResolvedAgentModel: {
      characteristics: components["schemas"]["HarnessModelCharacteristics"];
      /** Model Id */
      model_id: string;
      /** Model Key */
      model_key: string;
      /** Settings */
      settings: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** ResolvedSkillBinding */
    ResolvedSkillBinding: {
      /** Skill Id */
      skill_id: string;
      /** Skill Key */
      skill_key: string;
      /** Version */
      version?: number | null;
    };
    /** ResolvedSubagentEdge */
    ResolvedSubagentEdge: {
      /** Child Agent Id */
      child_agent_id: string;
      /** Child Agent Revision Id */
      child_agent_revision_id: string;
      context: components["schemas"]["DelegationContextPolicy"];
      /** Description */
      description?: string | null;
      environment: components["schemas"]["ChildEnvironmentPolicy"];
      /** Name */
      name: string;
      usage_limits?: components["schemas"]["UsageLimits-Output"] | null;
    };
    /** ResourceLifecycleEventPage */
    ResourceLifecycleEventPage: {
      /** High Watermark Resource Seq */
      high_watermark_resource_seq: number;
      /** Items */
      items: components["schemas"]["LifecycleEvent"][];
      /** Next Resource Seq */
      next_resource_seq: number;
      /** Resource Id */
      resource_id: string;
      /**
       * Resource Type
       * @enum {string}
       */
      resource_type: "run" | "run_attempt";
      /** Retained Resource Seq Floor */
      retained_resource_seq_floor: number;
    };
    /** RespondPendingResolution */
    RespondPendingResolution: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "respond";
      /** Call Id */
      call_id: string;
      response: components["schemas"]["JsonValue"];
    };
    /** RestoreAgentRevisionRequest */
    RestoreAgentRevisionRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /**
     * ResumeEntry
     * @description A per-interrupt response in the resume array of a RunAgentInput.
     */
    ResumeEntry: {
      /** Interruptid */
      interruptId: string;
      /** Payload */
      payload?: unknown | null;
      /**
       * Status
       * @enum {string}
       */
      status: "resolved" | "cancelled";
    } & {
      [key: string]: unknown;
    };
    /** RetentionPolicy */
    RetentionPolicy: {
      idle: components["schemas"]["RetentionWindow"];
    };
    /** RetentionWindow */
    RetentionWindow: {
      /** Delete After */
      delete_after: number | null;
      /** Stop After */
      stop_after: number | null;
    };
    /** RetryConfig */
    RetryConfig: {
      /**
       * Output
       * @default 0
       */
      output?: number;
      /**
       * Tools
       * @default 0
       */
      tools?: number;
    };
    /** RetryOverride */
    RetryOverride: {
      /** Output */
      output?: number | null;
      /** Tools */
      tools?: number | null;
    };
    /** RetryRunRequest */
    RetryRunRequest: {
      /** Expected Thread Version */
      expected_thread_version: number;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
    };
    /** RoleBinding */
    RoleBinding: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Id */
      id: string;
      /** Organization Id */
      organization_id: string;
      /** Principal Id */
      principal_id: string;
      /** Principal Type */
      principal_type: string;
      /** Resource Id */
      resource_id: string;
      /** Resource Type */
      resource_type: string;
      /** Role Key */
      role_key: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** RunAcceptanceReceipt */
    RunAcceptanceReceipt: {
      /** Hook Subscription Id */
      hook_subscription_id?: string | null;
      /** Run Id */
      run_id: string;
      /** Run Version */
      run_version: number;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /** Session Id */
      session_id: string;
      /**
       * Status
       * @default accepted
       * @constant
       */
      status?: "accepted";
      /** Thread Id */
      thread_id: string;
      /** Thread Version */
      thread_version: number;
    };
    /**
     * RunAgentInput
     * @description Input for running an agent.
     */
    RunAgentInput: {
      /** Context */
      context: components["schemas"]["Context"][];
      /** Forwardedprops */
      forwardedProps: unknown;
      /** Messages */
      messages: (
        | components["schemas"]["DeveloperMessage"]
        | components["schemas"]["SystemMessage"]
        | components["schemas"]["AssistantMessage"]
        | components["schemas"]["UserMessage"]
        | components["schemas"]["ToolMessage"]
        | components["schemas"]["ActivityMessage"]
        | components["schemas"]["ReasoningMessage"]
      )[];
      /** Parentrunid */
      parentRunId?: string | null;
      /** Resume */
      resume?: components["schemas"]["ResumeEntry"][] | null;
      /** Runid */
      runId: string;
      /** State */
      state: unknown;
      /** Threadid */
      threadId: string;
      /** Tools */
      tools: components["schemas"]["Tool"][];
    } & {
      [key: string]: unknown;
    };
    /** RunAttemptCollection */
    RunAttemptCollection: {
      /** Items */
      items: components["schemas"]["RunAttemptResource"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** RunAttemptResource */
    RunAttemptResource: {
      /** Attempt Number */
      attempt_number: number;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      failure: components["schemas"]["JsonValue"] | null;
      /** Finished At */
      finished_at: string | null;
      /** Harness Run Id */
      harness_run_id: string | null;
      /** Id */
      id: string;
      /** Replaces Run Attempt Id */
      replaces_run_attempt_id: string | null;
      /** Run Id */
      run_id: string;
      /** Start Reason */
      start_reason: string | null;
      /** Started At */
      started_at: string | null;
      /** Status */
      status: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Worker Build Id */
      worker_build_id: string;
      /** Yield Reason */
      yield_reason: string | null;
    };
    /** RunCollection */
    RunCollection: {
      /** Items */
      items: components["schemas"]["RunResource"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** RunLineage */
    RunLineage: {
      /** Head Run Id */
      head_run_id: string;
      /** Items */
      items: components["schemas"]["RunLineageEntry"][];
    };
    /** RunLineageEntry */
    RunLineageEntry: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Depth From Head */
      depth_from_head: number;
      lineage_kind: components["schemas"]["RunLineageKind"];
      /** Parent Run Id */
      parent_run_id: string | null;
      /** Run Id */
      run_id: string;
      /** Session Id */
      session_id: string;
      status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
    };
    /**
     * RunLineageKind
     * @enum {string}
     */
    RunLineageKind: "root" | "continue" | "fork";
    /** RunOutputAssetSource */
    RunOutputAssetSource: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "run_output";
      /** Run Id */
      run_id?: string | null;
    };
    /** RunResource */
    RunResource: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Completed At */
      completed_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Effective Agent Config Digest */
      effective_agent_config_digest: string;
      /** Environment Access */
      environment_access: string | null;
      /** Environment Id */
      environment_id: string | null;
      failure: components["schemas"]["JsonValue"] | null;
      /** Id */
      id: string;
      input: components["schemas"]["JsonValue"] | null;
      /** Input Kind */
      input_kind: string;
      /** Input Text */
      input_text: string | null;
      lineage_kind: components["schemas"]["RunLineageKind"];
      output: components["schemas"]["JsonValue"] | null;
      /** Output Text */
      output_text: string | null;
      /** Parent Run Id */
      parent_run_id: string | null;
      pending: components["schemas"]["JsonValue"] | null;
      /** Retry Of Run Id */
      retry_of_run_id: string | null;
      /** Sealed At */
      sealed_at: string | null;
      /** Sealed State Digest Sha256 */
      sealed_state_digest_sha256: string | null;
      /** Session Id */
      session_id: string;
      /** Started At */
      started_at: string | null;
      status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
      /** Trigger Type */
      trigger_type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Wait Reason */
      wait_reason: string | null;
      /** Waiting At */
      waiting_at: string | null;
    };
    /**
     * RunStatus
     * @enum {string}
     */
    RunStatus:
      "accepted" | "running" | "waiting" | "completed" | "failed" | "cancelled";
    /** RunStreamEvent */
    RunStreamEvent: {
      /** Event Id */
      event_id: string;
      /** Event Type */
      event_type: string;
      /**
       * Harness Run Id
       * @default null
       */
      harness_run_id?: string | null;
      /**
       * Item Id
       * @default null
       */
      item_id?: string | null;
      /**
       * Lifecycle Event Id
       * @default null
       */
      lifecycle_event_id?: string | null;
      /**
       * Occurred At
       * Format: date-time
       */
      occurred_at: string;
      /** Payload */
      payload: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Run Attempt Id
       * @default null
       */
      run_attempt_id?: string | null;
      /** Run Id */
      run_id: string;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /** Thread Id */
      thread_id: string;
    };
    /**
     * SafeFailure
     * @description Bounded serializable failure safe to expose outside the process.
     */
    SafeFailure: {
      /** Code */
      code: string;
      /**
       * Details
       * @description Return detached safe failure details.
       */
      readonly details: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Message */
      message: string;
      /**
       * Retry Hint
       * @default none
       * @enum {string}
       */
      retry_hint?: "none" | "new_run" | "dependency_change";
    };
    /** SearchConfiguration */
    SearchConfiguration: Record<string, never>;
    /**
     * SearchIn
     * @enum {string}
     */
    SearchIn: "input" | "output" | "input_output";
    /** SearchProvider */
    SearchProvider: {
      /** Configuration */
      configuration: {
        [key: string]: unknown;
      };
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Type */
      type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      updated_by: components["schemas"]["PrincipalRef"];
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** SearchProviderCollection */
    SearchProviderCollection: {
      /** Items */
      items: components["schemas"]["SearchProvider"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** SearchProviderDefinition */
    SearchProviderDefinition: {
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: unknown;
      };
      /**
       * Credential Required
       * @default true
       */
      credential_required?: boolean;
      /** Credential Schema */
      credential_schema: {
        [key: string]: unknown;
      };
      /** Display Name */
      display_name: string;
      /** Setup Url */
      setup_url: string;
      /** Type */
      type: string;
    };
    /** SearchProviderDefinitionCollection */
    SearchProviderDefinitionCollection: {
      /** Items */
      items: components["schemas"]["SearchProviderDefinition"][];
    };
    /** SearchProviderReference */
    SearchProviderReference: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Is Current */
      is_current: boolean;
      /** Version */
      version: number;
    };
    /** SearchProviderReferenceCollection */
    SearchProviderReferenceCollection: {
      /** Items */
      items: components["schemas"]["SearchProviderReference"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** SearchProviderTestResult */
    SearchProviderTestResult: {
      /**
       * Checked At
       * Format: date-time
       */
      checked_at: string;
      /** Code */
      code: string | null;
      /** Success */
      success: boolean;
    };
    /** SearchSelection */
    SearchSelection: {
      /**
       * Include Domains
       * @default []
       */
      include_domains?: string[];
      /**
       * Max Results
       * @default 5
       */
      max_results?: number;
      /** Provider Id */
      provider_id: string;
    };
    /** SecretRequirement */
    SecretRequirement: {
      /** Description */
      description?: string | null;
      /** Key */
      key: string;
      /**
       * Required
       * @default true
       */
      required?: boolean;
    };
    /** SecurityEvent */
    SecurityEvent: {
      /** Action */
      action: string;
      /** Actor Id */
      actor_id: string | null;
      /** Actor Type */
      actor_type: string;
      /** Id */
      id: string;
      /**
       * Occurred At
       * Format: date-time
       */
      occurred_at: string;
      /** Organization Id */
      organization_id: string | null;
      /** Outcome */
      outcome: string;
      /** Request Id */
      request_id: string | null;
      /** Resource Id */
      resource_id: string | null;
      /** Resource Type */
      resource_type: string | null;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** ServiceAccount */
    ServiceAccount: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Deleted At */
      deleted_at: string | null;
      /** Description */
      description: string | null;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Role */
      role: string;
      /** Status */
      status: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** SessionCollection */
    SessionCollection: {
      /** Items */
      items: components["schemas"]["SessionResource"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SessionPreview */
    SessionPreview: {
      /** Input Text */
      input_text: string | null;
      /** Output Text */
      output_text: string | null;
      /** Run Id */
      run_id: string;
      /** Thread Id */
      thread_id: string;
    };
    /** SessionResource */
    SessionResource: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Id */
      id: string;
      preview: components["schemas"]["SessionPreview"] | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** SetRoleRequest */
    SetRoleRequest: {
      /** Principal Id */
      principal_id: string;
      /**
       * Role
       * @enum {string}
       */
      role: "member" | "viewer" | "runner" | "builder" | "admin";
    };
    /** Skill */
    Skill: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Current Revision Id */
      current_revision_id: string;
      /** Deleted At */
      deleted_at: string | null;
      /** Id */
      id: string;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      updated_by: components["schemas"]["PrincipalRef"];
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** SkillAgentReference */
    SkillAgentReference: {
      /** Agent Id */
      agent_id: string;
      /** Agent Key */
      agent_key: string;
      /** Agent Name */
      agent_name: string;
      /** Agent Revision Id */
      agent_revision_id: string;
    };
    /** SkillAgentReferenceCollection */
    SkillAgentReferenceCollection: {
      /** Items */
      items: components["schemas"]["SkillAgentReference"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SkillCollection */
    SkillCollection: {
      /** Items */
      items: components["schemas"]["Skill"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SkillPackageFile */
    SkillPackageFile: {
      /** Path */
      path: string;
      /** Sha256 */
      sha256: string;
      /** Size Bytes */
      size_bytes: number;
    };
    /** SkillPackageManifest */
    SkillPackageManifest: {
      /** Content Digest */
      content_digest: string;
      /** Description */
      description: string;
      /** Files */
      files: components["schemas"]["SkillPackageFile"][];
      /**
       * Harness Skill Contract
       * @default 1
       * @constant
       */
      harness_skill_contract?: "1";
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /** Skill Name */
      skill_name: string;
      /** Total Size Bytes */
      total_size_bytes: number;
    };
    /** SkillPublicationReceipt */
    SkillPublicationReceipt: {
      /**
       * Outcome
       * @enum {string}
       */
      outcome: "published" | "already_current";
      revision: components["schemas"]["SkillRevision"];
      skill: components["schemas"]["Skill"];
    };
    /** SkillRevision */
    SkillRevision: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Id */
      id: string;
      /** Imported From */
      imported_from:
        | components["schemas"]["ZipSkillImportProvenance"]
        | components["schemas"]["GitHubSkillImportProvenance"];
      manifest: components["schemas"]["SkillPackageManifest"];
      /** Organization Id */
      organization_id: string;
      /** Skill Id */
      skill_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** SkillRevisionCollection */
    SkillRevisionCollection: {
      /** Items */
      items: components["schemas"]["SkillRevision"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SkillSelection */
    SkillSelection: {
      /** Skill Key */
      skill_key: string;
      /** Version */
      version?: number | null;
    };
    /** SkillUploadReceipt */
    SkillUploadReceipt: {
      /** Archive Sha256 */
      archive_sha256: string;
      /** Consumed By Revision Id */
      consumed_by_revision_id: string | null;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      manifest: components["schemas"]["SkillPackageManifest"];
      /** Upload Id */
      upload_id: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** StartConnectorConnectionSetupRequest */
    StartConnectorConnectionSetupRequest: {
      /** Browser Nonce */
      browser_nonce?: string | null;
      /** Expected Version */
      expected_version: number;
      /** Return Path */
      return_path: string;
      /** Setup */
      setup: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** StartRunRequest */
    StartRunRequest: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      config_override?: components["schemas"]["AgentRunOverride-Input"] | null;
      environment?: components["schemas"]["EnvironmentSelection"] | null;
      /** Expected Current Revision Id */
      expected_current_revision_id?: string | null;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
      input: components["schemas"]["AgentInput"];
      /** Session Id */
      session_id?: string | null;
    };
    /** SteerReceipt */
    SteerReceipt: {
      /**
       * Accepted At
       * Format: date-time
       */
      accepted_at: string;
      /** Delivery Sequence */
      delivery_sequence: number;
      /** Run Id */
      run_id: string;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /** Session Id */
      session_id: string;
      /** Steer Id */
      steer_id: string;
      /** Thread Id */
      thread_id: string;
    };
    /** SteerStatus */
    SteerStatus: {
      /** Accepted Against Run Id */
      accepted_against_run_id: string;
      /** Consumed By Run Id */
      consumed_by_run_id: string | null;
      /** Consumed Checkpoint Seq */
      consumed_checkpoint_seq: number | null;
      /** Consumed State Digest Sha256 */
      consumed_state_digest_sha256: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Delivery Sequence */
      delivery_sequence: number;
      /** Finalized At */
      finalized_at: string | null;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: "1";
      /** Session Id */
      session_id: string;
      /** Source Waiting Run Id */
      source_waiting_run_id: string | null;
      /**
       * Status
       * @enum {string}
       */
      status: "pending" | "consumed" | "superseded";
      /** Steer Id */
      steer_id: string;
      /** Target Run Id */
      target_run_id: string | null;
      /** Thread Id */
      thread_id: string;
    };
    /** SubagentOverride */
    "SubagentOverride-Input": {
      /** Agent Id */
      agent_id?: string | null;
      context?: components["schemas"]["DelegationContextPolicy"] | null;
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"] | null;
      usage_limits?: components["schemas"]["UsageLimits-Input"] | null;
      /** Version */
      version?: number | null;
    };
    /** SubagentOverride */
    "SubagentOverride-Output": {
      /** Agent Id */
      agent_id?: string | null;
      context?: components["schemas"]["DelegationContextPolicy"] | null;
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"] | null;
      usage_limits?: components["schemas"]["UsageLimits-Output"] | null;
      /** Version */
      version?: number | null;
    };
    /** SubagentSelection */
    "SubagentSelection-Input": {
      /** Agent Id */
      agent_id: string;
      context?: components["schemas"]["DelegationContextPolicy"];
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"];
      usage_limits?: components["schemas"]["UsageLimits-Input"] | null;
      /** Version */
      version?: number | null;
    };
    /** SubagentSelection */
    "SubagentSelection-Output": {
      /** Agent Id */
      agent_id: string;
      context?: components["schemas"]["DelegationContextPolicy"];
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"];
      usage_limits?: components["schemas"]["UsageLimits-Output"] | null;
      /** Version */
      version?: number | null;
    };
    /**
     * SystemActorRef
     * @description Historical system attribution; never an authenticatable Principal.
     */
    SystemActorRef: {
      /** Principal Id */
      principal_id: string;
      /**
       * Principal Type
       * @default system
       * @constant
       */
      principal_type?: "system";
    };
    /**
     * SystemMessage
     * @description A system message.
     */
    SystemMessage: {
      /** Content */
      content: string;
      /** Encryptedvalue */
      encryptedValue?: string | null;
      /** Id */
      id: string;
      /** Name */
      name?: string | null;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      role: "system";
    } & {
      [key: string]: unknown;
    };
    /** TargetCollection */
    TargetCollection: {
      /** Items */
      items: components["schemas"]["AccountTarget"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** TargetConfig */
    TargetConfig: {
      /** Agent Id */
      agent_id?: string | null;
      config_override?: components["schemas"]["InputOverride"] | null;
      /** External Target Id */
      external_target_id: string;
      input_batching?: components["schemas"]["InputBatchingPolicy"] | null;
      /** Provider Policy */
      provider_policy?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Receive Enabled
       * @default true
       */
      receive_enabled?: boolean;
      /**
       * Target Kind
       * @enum {string}
       */
      target_kind: "conversation" | "repository";
    };
    /** TextContent */
    TextContent: {
      /** Text */
      text: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "text";
    };
    /**
     * TextInputContent
     * @description A text fragment in a multimodal user message.
     */
    TextInputContent: {
      /** Text */
      text: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "text";
    } & {
      [key: string]: unknown;
    };
    /** Thread */
    Thread: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Run Id */
      current_run_id?: string | null;
      /** Default Environment Id */
      default_environment_id?: string | null;
      /** Head Run Id */
      head_run_id?: string | null;
      /** Id */
      id: string;
      /** Organization Id */
      organization_id: string;
      origin_kind: components["schemas"]["ThreadOriginKind"];
      /** Origin Run Id */
      origin_run_id?: string | null;
      /** Origin Thread Id */
      origin_thread_id?: string | null;
      /** Queue Version */
      queue_version: number;
      role: components["schemas"]["ThreadRole"];
      /** Session Id */
      session_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /** ThreadCollection */
    ThreadCollection: {
      /** Items */
      items: components["schemas"]["ThreadResource"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ThreadOriginKind
     * @enum {string}
     */
    ThreadOriginKind: "new" | "fork" | "child";
    /** ThreadQueueMutationReceipt */
    ThreadQueueMutationReceipt: {
      /** Queue Version */
      queue_version: number;
      /** Thread Id */
      thread_id: string;
    };
    /** ThreadResource */
    ThreadResource: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Run Id */
      current_run_id: string | null;
      /** Default Environment Id */
      default_environment_id: string | null;
      /** Head Run Id */
      head_run_id: string | null;
      /** Id */
      id: string;
      /** Origin Kind */
      origin_kind: string;
      /** Origin Run Id */
      origin_run_id: string | null;
      /** Origin Thread Id */
      origin_thread_id: string | null;
      /** Queue Version */
      queue_version: number;
      /** Role */
      role: string;
      /** Session Id */
      session_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /**
     * ThreadRole
     * @enum {string}
     */
    ThreadRole: "root" | "child";
    /** ThreadRunSubmissionIntent */
    "ThreadRunSubmissionIntent-Input": {
      /** Agent Id */
      agent_id?: string | null;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      config_override?: components["schemas"]["AgentRunOverride-Input"] | null;
      environment?: components["schemas"]["EnvironmentSelection"] | null;
      /** Expected Current Revision Id */
      expected_current_revision_id?: string | null;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
      input: components["schemas"]["AgentInput"];
    };
    /** ThreadRunSubmissionIntent */
    "ThreadRunSubmissionIntent-Output": {
      /** Agent Id */
      agent_id?: string | null;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      config_override?: components["schemas"]["AgentRunOverride-Output"] | null;
      environment?: components["schemas"]["EnvironmentSelection"] | null;
      /** Expected Current Revision Id */
      expected_current_revision_id?: string | null;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
      input: components["schemas"]["AgentInput"];
    };
    /** ThreadRunSubmissionReceipt */
    ThreadRunSubmissionReceipt: {
      /**
       * Outcome
       * @enum {string}
       */
      outcome: "run_accepted" | "queued";
      /** Queue Version */
      queue_version: number;
      queued_submission?: components["schemas"]["QueuedSubmission"] | null;
      run?: components["schemas"]["RunAcceptanceReceipt"] | null;
    };
    /** ThreadRunSubmissionRequest */
    ThreadRunSubmissionRequest: {
      /** Agent Id */
      agent_id?: string | null;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      config_override?: components["schemas"]["AgentRunOverride-Input"] | null;
      environment?: components["schemas"]["EnvironmentSelection"] | null;
      /** Expected Current Revision Id */
      expected_current_revision_id?: string | null;
      /** Expected Thread Version */
      expected_thread_version: number;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
      input: components["schemas"]["AgentInput"];
      waiting_resolution?:
        components["schemas"]["WaitingResolutionDefaults"] | null;
    };
    /**
     * Tool
     * @description A tool definition.
     */
    Tool: {
      /** Description */
      description: string;
      /** Name */
      name: string;
      /** Parameters */
      parameters?: unknown | null;
    } & {
      [key: string]: unknown;
    };
    /**
     * ToolCall
     * @description A tool call, modelled after OpenAI tool calls.
     */
    ToolCall: {
      /** Encryptedvalue */
      encryptedValue?: string | null;
      function: components["schemas"]["FunctionCall"];
      /** Id */
      id: string;
      /**
       * Type
       * @default function
       * @constant
       */
      type?: "function";
    } & {
      [key: string]: unknown;
    };
    /**
     * ToolMessage
     * @description A tool result message.
     */
    ToolMessage: {
      /** Content */
      content: string;
      /** Encryptedvalue */
      encryptedValue?: string | null;
      /** Error */
      error?: string | null;
      /** Id */
      id: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      role: "tool";
      /** Toolcallid */
      toolCallId: string;
    } & {
      [key: string]: unknown;
    };
    /** TraceCollection */
    TraceCollection: {
      /** Items */
      items: components["schemas"]["TraceSummary"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** TraceDetail */
    TraceDetail: {
      /** Observations */
      observations: components["schemas"]["Observation"][];
      trace: components["schemas"]["TraceSummary"];
    };
    /** TraceSummary */
    TraceSummary: {
      /** Duration Ms */
      duration_ms: number | null;
      /** Ended At */
      ended_at: string | null;
      /** Id */
      id: string;
      input: components["schemas"]["JsonValue"] | null;
      /** Models */
      models: string[];
      /** Name */
      name: string;
      /** Observation Count */
      observation_count: number | null;
      output: components["schemas"]["JsonValue"] | null;
      /** Run Attempt Id */
      run_attempt_id: string;
      /** Run Attempt Number */
      run_attempt_number: number;
      /** Run Attempt Outcome */
      run_attempt_outcome:
        ("succeeded" | "yielded" | "failed" | "cancelled") | null;
      /** Run Id */
      run_id: string;
      /** Session Id */
      session_id: string;
      /** Source Url */
      source_url: string | null;
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
      /** Thread Id */
      thread_id: string;
      /** Total Cost Usd */
      total_cost_usd: string | null;
      /**
       * Trace Status
       * @enum {string}
       */
      trace_status: "unset" | "ok" | "error";
      /** Usage */
      usage: {
        [key: string]: number;
      } | null;
    };
    /**
     * TraceView
     * @enum {string}
     */
    TraceView: "compact" | "full";
    /** UpdateAccountRequest */
    UpdateAccountRequest: {
      /** Default Agent Id */
      default_agent_id?: string | null;
      /** Execution Service Account Id */
      execution_service_account_id?: string | null;
      /** Expected Version */
      expected_version: number;
      input_batching?: components["schemas"]["InputBatchingPolicy"] | null;
      /** Name */
      name?: string | null;
      /** Provider Config */
      provider_config?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Provider Policy */
      provider_policy?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Receive Enabled */
      receive_enabled?: boolean | null;
    };
    /** UpdateAgentRequest */
    UpdateAgentRequest: {
      /** Default Environment Template Id */
      default_environment_template_id?: string | null;
      /** Description */
      description?: string | null;
      /** Key */
      key?: string | null;
      /** Name */
      name?: string | null;
    };
    /** UpdateConnectorConnectionRequest */
    UpdateConnectorConnectionRequest: {
      /** Expected Version */
      expected_version: number;
      /** Name */
      name?: string | null;
    };
    /** UpdateConnectorProviderRequest */
    UpdateConnectorProviderRequest: {
      /** Expected Version */
      expected_version: number;
      /** Name */
      name?: string | null;
    };
    /** UpdateHookSubscriptionRequest */
    UpdateHookSubscriptionRequest: {
      /** Hook Names */
      hook_names: string[];
      /** Run Id */
      run_id?: string | null;
      /** Session Id */
      session_id?: string | null;
      /** Thread Id */
      thread_id?: string | null;
      webhook: components["schemas"]["WebhookDestinationConfig"];
    };
    /** UpdateHookSubscriptionStateRequest */
    UpdateHookSubscriptionStateRequest: {
      /** Enabled */
      enabled: boolean;
    };
    /** UpdateMCPConnectionRequest */
    UpdateMCPConnectionRequest: {
      /** Expected Version */
      expected_version: number;
      /** Name */
      name: string;
    };
    /** UpdateModelProviderRequest */
    UpdateModelProviderRequest: {
      /** Configuration */
      configuration?: {
        [key: string]: unknown;
      } | null;
      /** Credential */
      credential?: string | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Name */
      name?: string | null;
    };
    /** UpdateModelRequest */
    UpdateModelRequest: {
      /** Description */
      description?: string | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Model Api */
      model_api?: string | null;
      /** Name */
      name?: string | null;
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Upstream Model */
      upstream_model?: string | null;
    };
    /** UpdateProfileRequest */
    UpdateProfileRequest: {
      /** Name */
      name: string;
    };
    /** UpdateProviderRequest */
    UpdateProviderRequest: {
      /** Enabled */
      enabled?: boolean | null;
      /** Name */
      name?: string | null;
    };
    /** UpdateQueuedSubmissionRequest */
    UpdateQueuedSubmissionRequest: {
      /** Expected Version */
      expected_version: number;
      submission: components["schemas"]["ThreadRunSubmissionIntent-Input"];
    };
    /** UpdateResourceProfileRequest */
    UpdateResourceProfileRequest: {
      /** Key */
      key?: string | null;
      /** Name */
      name?: string | null;
    };
    /** UpdateSearchProviderRequest */
    UpdateSearchProviderRequest: {
      configuration?: components["schemas"]["SearchConfiguration"] | null;
      /** Credential */
      credential?: string | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Name */
      name?: string | null;
    };
    /** UpdateServiceAccountRequest */
    UpdateServiceAccountRequest: {
      /** Description */
      description?: string | null;
      /** Expected Version */
      expected_version: number;
      /** Name */
      name: string;
      /**
       * Role
       * @enum {string}
       */
      role: "viewer" | "runner" | "builder";
      /**
       * Status
       * @enum {string}
       */
      status: "active" | "disabled";
    };
    /** UpdateSkillRequest */
    UpdateSkillRequest: {
      /** Name */
      name: string;
    };
    /** UpdateTemplateRequest */
    UpdateTemplateRequest: {
      /** Archived */
      archived?: boolean | null;
      /** Description */
      description?: string | null;
      /** Name */
      name?: string | null;
    };
    /** UploadedAssetSource */
    UploadedAssetSource: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "upload";
      principal: components["schemas"]["PrincipalRef"];
    };
    /** UrlBinarySource */
    UrlBinarySource: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "url";
      /** Url */
      url: string;
    };
    /**
     * UsageLimits
     * @description Limits on model usage.
     *
     *     The request count is tracked by pydantic_ai, and the request limit is checked before each request to the model.
     *     Token counts are provided in responses from the model, and the token limits are checked after each response.
     *
     *     Each of the limits can be set to `None` to disable that limit.
     */
    "UsageLimits-Input": {
      /** Cost Limit */
      cost_limit?: number | string | null;
      /**
       * Count Tokens Before Request
       * @default false
       */
      count_tokens_before_request?: boolean;
      /** Input Tokens Limit */
      input_tokens_limit?: number | null;
      /** Output Tokens Limit */
      output_tokens_limit?: number | null;
      /** Per Request Input Tokens Limit */
      per_request_input_tokens_limit?: number | null;
      /**
       * Request Limit
       * @default 50
       */
      request_limit?: number | null;
      /** Tool Calls Limit */
      tool_calls_limit?: number | null;
      /** Total Tokens Limit */
      total_tokens_limit?: number | null;
    };
    /**
     * UsageLimits
     * @description Limits on model usage.
     *
     *     The request count is tracked by pydantic_ai, and the request limit is checked before each request to the model.
     *     Token counts are provided in responses from the model, and the token limits are checked after each response.
     *
     *     Each of the limits can be set to `None` to disable that limit.
     */
    "UsageLimits-Output": {
      /** Cost Limit */
      cost_limit?: string | null;
      /**
       * Count Tokens Before Request
       * @default false
       */
      count_tokens_before_request?: boolean;
      /** Input Tokens Limit */
      input_tokens_limit?: number | null;
      /** Output Tokens Limit */
      output_tokens_limit?: number | null;
      /** Per Request Input Tokens Limit */
      per_request_input_tokens_limit?: number | null;
      /**
       * Request Limit
       * @default 50
       */
      request_limit?: number | null;
      /** Tool Calls Limit */
      tool_calls_limit?: number | null;
      /** Total Tokens Limit */
      total_tokens_limit?: number | null;
    };
    /** User */
    User: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Email */
      email: string;
      /** Email Verified At */
      email_verified_at: string | null;
      /** Id */
      id: string;
      /** Image Url */
      readonly image_url: string | null;
      /** Name */
      name: string;
      /** Status */
      status: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
    };
    /**
     * UserMessage
     * @description A user message supporting text or multimodal content.
     */
    UserMessage: {
      /** Content */
      content:
        | string
        | (
            | components["schemas"]["TextInputContent"]
            | components["schemas"]["ImageInputContent"]
            | components["schemas"]["AudioInputContent"]
            | components["schemas"]["VideoInputContent"]
            | components["schemas"]["DocumentInputContent"]
            | components["schemas"]["BinaryInputContent"]
          )[];
      /** Encryptedvalue */
      encryptedValue?: string | null;
      /** Id */
      id: string;
      /** Name */
      name?: string | null;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      role: "user";
    } & {
      [key: string]: unknown;
    };
    /** ValidationError */
    ValidationError: {
      /** Context */
      ctx?: Record<string, never>;
      /** Input */
      input?: unknown;
      /** Location */
      loc: (string | number)[];
      /** Message */
      msg: string;
      /** Error Type */
      type: string;
    };
    /**
     * VideoInputContent
     * @description A video input content fragment.
     */
    VideoInputContent: {
      /** Metadata */
      metadata?: unknown | null;
      /** Source */
      source:
        | components["schemas"]["InputContentDataSource"]
        | components["schemas"]["InputContentUrlSource"];
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "video";
    } & {
      [key: string]: unknown;
    };
    /** WaitingResolutionDefaults */
    WaitingResolutionDefaults: {
      /**
       * Mode
       * @default defaults
       * @constant
       */
      mode?: "defaults";
      /** Sealed State Digest Sha256 */
      sealed_state_digest_sha256: string;
    };
    /** WaitingRunFeedbackRequest */
    WaitingRunFeedbackRequest: {
      /** Expected Thread Version */
      expected_thread_version: number;
      hook_subscription?:
        components["schemas"]["InlineHookSubscriptionInput"] | null;
      /**
       * Resolutions
       * @default []
       */
      resolutions?: (
        | components["schemas"]["ApprovePendingResolution"]
        | components["schemas"]["RejectPendingResolution"]
        | components["schemas"]["CompletePendingResolution"]
        | components["schemas"]["RespondPendingResolution"]
      )[];
      /** Sealed State Digest Sha256 */
      sealed_state_digest_sha256: string;
    };
    /** WebhookDestinationConfig */
    WebhookDestinationConfig: {
      /** Endpoint Url */
      endpoint_url: string;
      /**
       * Signature Profile
       * @default hmac_sha256_v1
       * @constant
       */
      signature_profile?: "hmac_sha256_v1";
      /** Signing Secret Id */
      signing_secret_id: string;
    };
    /** Workspace */
    Workspace: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Id */
      id: string;
      /** Image Url */
      readonly image_url: string | null;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
    };
    /** WorkspaceEventPage */
    WorkspaceEventPage: {
      /** High Watermark */
      high_watermark: string;
      /** Items */
      items: components["schemas"]["LifecycleEvent"][];
      /** Next Cursor */
      next_cursor: string | null;
      /** Retained Floor */
      retained_floor: string;
    };
    /** WorkspaceSecretCredential */
    WorkspaceSecretCredential: {
      /** Secret Id */
      secret_id: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      source: "workspace_secret";
    };
    /** ZipSkillImportProvenance */
    ZipSkillImportProvenance: {
      /** Archive Sha256 */
      archive_sha256: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "zip";
    };
    /** ZipUploadSkillSource */
    ZipUploadSkillSource: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "zip_upload";
      /** Upload Id */
      upload_id: string;
    };
  };
  responses: never;
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
  get_agent_revision_api_v1_agent_revisions__agent_revision_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        agent_revision_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevision"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  key_metadata_api_v1_api_keys__key_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        key_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ApiKey"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  revoke_key_api_v1_api_keys__key_id__revoke_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        key_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ApiKey"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_account_api_v1_application_accounts__account_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_account_api_v1_application_accounts__account_id__delete: {
    parameters: {
      query: {
        expected_version: number;
      };
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_account_api_v1_application_accounts__account_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateAccountRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replace_account_credentials_api_v1_application_accounts__account_id__credentials_put: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplaceAccountCredentialsRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_targets_api_v1_application_accounts__account_id__targets_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TargetCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_api_v1_application_accounts__account_id__targets_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TargetConfig"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountTarget"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_api_v1_application_accounts__account_id__targets__target_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        target_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountTarget"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replace_api_v1_application_accounts__account_id__targets__target_id__put: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        target_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplaceTargetRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountTarget"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_api_v1_application_accounts__account_id__targets__target_id__delete: {
    parameters: {
      query: {
        expected_version: number;
      };
      header?: never;
      path: {
        account_id: string;
        target_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  change_account_lifecycle_api_v1_application_accounts__account_id___action__post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        account_id: string;
        action: "enable" | "disable";
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AccountCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_asset_api_v1_assets__asset_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Asset"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_asset_api_v1_assets__asset_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_asset_content_api_v1_assets__asset_id__content_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  auth_configuration_api_v1_auth_configuration_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthConfiguration"];
        };
      };
    };
  };
  credential_context_api_v1_auth_context_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CredentialContext"];
        };
      };
    };
  };
  browser_proof_api_v1_auth_csrf_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": {
            [key: string]: string;
          };
        };
      };
    };
  };
  login_api_v1_auth_login_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LoginRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  logout_api_v1_auth_logout_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
    };
  };
  request_password_reset_api_v1_auth_password_reset_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PasswordResetRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  complete_password_reset_api_v1_auth_password_reset_complete_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CompletePasswordResetRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_connector_connection_api_v1_connector_connections__connection_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_connector_connection_api_v1_connector_connections__connection_id__delete: {
    parameters: {
      query: {
        expected_version: number;
      };
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionCleanupReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_connector_connection_api_v1_connector_connections__connection_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateConnectorConnectionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reconnect_connector_connection_api_v1_connector_connections__connection_id__reconnect_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReconnectConnectorConnectionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorSetupLaunch"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  revoke_connector_connection_api_v1_connector_connections__connection_id__revoke_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectorConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionCleanupReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  start_connector_connection_setup_api_v1_connector_connections__connection_id__setup_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["StartConnectorConnectionSetupRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorSetupLaunch"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  change_connector_connection_lifecycle_api_v1_connector_connections__connection_id___action__post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
        action: "enable" | "disable";
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectorConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_connector_provider_types_api_v1_connector_provider_types_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderDefinitionCollection"];
        };
      };
    };
  };
  get_connector_provider_api_v1_connector_providers__connector_provider_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connector_provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_connector_provider_api_v1_connector_providers__connector_provider_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connector_provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateConnectorProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  preview_connector_tools_api_v1_connector_providers__connector_provider_id__connectors__connector_key__tools_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connector_provider_id: string;
        connector_key: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorToolPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replace_connector_provider_credentials_api_v1_connector_providers__connector_provider_id__credentials_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connector_provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplaceConnectorProviderCredentialsRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  discover_connectors_api_v1_connector_providers__connector_provider_id__discover_connectors_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connector_provider_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  test_connector_provider_api_v1_connector_providers__connector_provider_id__test_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connector_provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectorProviderCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderTestResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  change_connector_provider_lifecycle_api_v1_connector_providers__connector_provider_id___action__post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connector_provider_id: string;
        action: "enable" | "disable";
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectorProviderCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  complete_connector_setup_api_v1_connector_setup_complete_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CompleteConnectorSetupRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorSetupCompletion"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_command_api_v1_environment_commands__command_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        command_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentCommand"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  provider_types_api_v1_environment_provider_types_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_dict_"];
        };
      };
    };
  };
  get_provider_type_api_v1_environment_provider_types__provider_type__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_type: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": {
            [key: string]: unknown;
          };
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_provider_api_v1_environment_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replace_credential_api_v1_environment_providers__provider_id__credential_put: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplaceCredentialRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_provider_api_v1_environment_providers__resource_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        resource_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_revision_api_v1_environment_template_revisions__revision_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        revision_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplateRevision"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_template_api_v1_environment_templates__resource_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        resource_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_template_api_v1_environment_templates__template_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        template_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateTemplateRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_revisions_api_v1_environment_templates__template_id__revisions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        template_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentTemplateRevision_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_revision_api_v1_environment_templates__template_id__revisions_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        template_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateTemplateRevisionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplateRevision"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_environment_api_v1_environments__environment_id__delete_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentCommand"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  stop_environment_api_v1_environments__environment_id__stop_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentCommand"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_environment_api_v1_environments__resource_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        resource_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Environment"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_hook_subscription_api_v1_hook_subscriptions__subscription_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        subscription_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_hook_subscription_api_v1_hook_subscriptions__subscription_id__put: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        subscription_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateHookSubscriptionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_hook_subscription_api_v1_hook_subscriptions__subscription_id__delete: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        subscription_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_hook_subscription_state_api_v1_hook_subscriptions__subscription_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        subscription_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateHookSubscriptionStateRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  redrive_hook_delivery_api_v1_hook_subscriptions__subscription_id__deliveries__delivery_id__redrive_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        subscription_id: string;
        delivery_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  accept_invitation_api_v1_invitations__invitation_id__accept_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        invitation_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AcceptInvitationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  resend_invitation_api_v1_invitations__invitation_id__resend_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        invitation_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ExpectedVersion"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationDelivery"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  revoke_invitation_api_v1_invitations__invitation_id__revoke_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        invitation_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ExpectedVersion"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Invitation"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_mcp_connection_api_v1_mcp_connections__connection_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_mcp_connection_api_v1_mcp_connections__connection_id__delete: {
    parameters: {
      query: {
        expected_version: number;
      };
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionCleanupReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_mcp_connection_api_v1_mcp_connections__connection_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateMCPConnectionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  authorize_mcp_connection_api_v1_mcp_connections__connection_id__authorize_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MCPConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPAuthorizationLaunch"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  replace_mcp_credentials_api_v1_mcp_connections__connection_id__credentials_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReplaceMCPCredentialsRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  discover_mcp_tools_api_v1_mcp_connections__connection_id__discover_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MCPConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPToolCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reconnect_mcp_connection_api_v1_mcp_connections__connection_id__reconnect_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MCPConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  change_mcp_connection_lifecycle_api_v1_mcp_connections__connection_id___action__post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        connection_id: string;
        action: "enable" | "disable";
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MCPConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_model_provider_types_api_v1_model_provider_types_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProviderDefinitionCollection"];
        };
      };
    };
  };
  mcp_oauth_callback_api_v1_oauth_mcp_callback_get: {
    parameters: {
      query: {
        code: string;
        state: string;
        iss: string;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  mcp_client_metadata_api_v1_oauth_mcp_client_metadata_json_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPClientMetadata"];
        };
      };
    };
  };
  organizations_api_v1_organizations_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Organization_"];
        };
      };
    };
  };
  organization_api_v1_organizations__organization__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_organization_api_v1_organizations__organization__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateResourceProfileRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_list_connector_providers_api_v1_organizations__organization__connector_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_create_connector_provider_api_v1_organizations__organization__connector_providers_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateConnectorProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_list_providers_api_v1_organizations__organization__environment_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentProvider_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_create_provider_api_v1_organizations__organization__environment_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_list_templates_api_v1_organizations__organization__environment_templates_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentTemplate_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_create_template_api_v1_organizations__organization__environment_templates_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateTemplateRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  put_organization_icon_api_v1_organizations__organization__icon_put: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/octet-stream": Binary;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_organization_icon_api_v1_organizations__organization__icon_delete: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_organization_icon_api_v1_organizations__organization__icon__image_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        image_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  invitations_api_v1_organizations__organization__invitations_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Invitation_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  invite_api_v1_organizations__organization__invitations_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateInvitationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationDelivery"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_list_model_providers_api_v1_organizations__organization__model_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        name?: string | null;
        provider_type?: string | null;
        enabled?: boolean | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProviderCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_create_model_provider_api_v1_organizations__organization__model_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateModelProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_get_model_provider_api_v1_organizations__organization__model_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_update_model_provider_api_v1_organizations__organization__model_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateModelProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_describe_provider_model_api_v1_organizations__organization__model_providers__provider_id__describe_model_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DescribeModelRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelDescription"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_discover_provider_models_api_v1_organizations__organization__model_providers__provider_id__discover_models_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelDiscovery"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_test_model_provider_api_v1_organizations__organization__model_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_list_models_api_v1_organizations__organization__models_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        query?: string | null;
        provider_id?: string | null;
        enabled?: boolean | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_create_model_api_v1_organizations__organization__models_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateModelRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_get_model_api_v1_organizations__organization__models__model_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        model_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_update_model_api_v1_organizations__organization__models__model_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        model_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateModelRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_test_model_api_v1_organizations__organization__models__model_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        model_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: {
      content: {
        "application/json": components["schemas"]["ModelTestRequest"] | null;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_permissions_api_v1_organizations__organization__permissions_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OrganizationPermissions"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_roles_api_v1_organizations__organization__role_bindings_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_RoleBinding_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_organization_binding_api_v1_organizations__organization__role_bindings_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetRoleRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_organization_provider_api_v1_organizations__organization__search_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        type?: string | null;
        enabled?: boolean | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_organization_provider_api_v1_organizations__organization__search_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateSearchProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_organization_provider_api_v1_organizations__organization__search_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_organization_provider_api_v1_organizations__organization__search_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header?: {
        "If-Match"?: string | null;
      };
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateSearchProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  references_organization_provider_api_v1_organizations__organization__search_providers__provider_id__references_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderReferenceCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  test_organization_provider_api_v1_organizations__organization__search_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SearchConfiguration"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderTestResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  organization_security_events_api_v1_organizations__organization__security_audit_events_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_SecurityEvent_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  users_api_v1_organizations__organization__users_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_User_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspaces_api_v1_organizations__organization__workspaces_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Workspace_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_workspace_api_v1_organizations__organization__workspaces_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateWorkspaceRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_queued_submission_api_v1_queued_submissions__queued_submission_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        queued_submission_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmission"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_queued_submission_api_v1_queued_submissions__queued_submission_id__delete: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        queued_submission_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DeleteQueuedSubmissionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadQueueMutationReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_queued_submission_api_v1_queued_submissions__queued_submission_id__patch: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        queued_submission_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateQueuedSubmissionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmissionMutationReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  role_binding_api_v1_role_bindings__binding_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        binding_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  remove_member_api_v1_role_bindings__binding_id__delete: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        binding_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  change_role_api_v1_role_bindings__binding_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        binding_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ChangeRoleRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_run_attempt_api_v1_run_attempts__run_attempt_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        run_attempt_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAttemptResource"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_run_attempt_events_api_v1_run_attempts__run_attempt_id__events_get: {
    parameters: {
      query?: {
        after_resource_seq?: number;
        limit?: number;
      };
      header?: never;
      path: {
        run_attempt_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ResourceLifecycleEventPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_run_api_v1_runs__run_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunResource"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_run_attempts_api_v1_runs__run_id__attempts_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAttemptCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_run_events_api_v1_runs__run_id__events_get: {
    parameters: {
      query?: {
        after_resource_seq?: number;
        limit?: number;
      };
      header?: never;
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ResourceLifecycleEventPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  feedback_run_api_v1_runs__run_id__feedback_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["WaitingRunFeedbackRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  fork_run_api_v1_runs__run_id__fork_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ForkRunRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  interrupt_run_api_v1_runs__run_id__interrupt_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["InterruptRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InterruptReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_run_items_api_v1_runs__run_id__items_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ItemCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_run_lineage_api_v1_runs__run_id__lineage_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunLineage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_pending_actions_api_v1_runs__run_id__pending_actions_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PendingActionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  retry_run_api_v1_runs__run_id__retry_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RetryRunRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  steer_run_api_v1_runs__run_id__steer_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SteerReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_run_steer_api_v1_runs__run_id__steers__steer_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        run_id: string;
        steer_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SteerStatus"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  stream_run_api_v1_runs__run_id__stream_get: {
    parameters: {
      query?: never;
      header?: {
        accept?: string | null;
        "Last-Event-ID"?: string | null;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  continue_from_run_api_v1_runs__source_run_id__continue_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        source_run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ContinueRunRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_types_api_v1_search_provider_types_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderDefinitionCollection"];
        };
      };
    };
  };
  get_type_api_v1_search_provider_types__provider_type__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_type: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderDefinition"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  account_api_v1_service_accounts__account_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ServiceAccount"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_account_api_v1_service_accounts__account_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ExpectedVersion"];
      };
    };
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_account_api_v1_service_accounts__account_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateServiceAccountRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ServiceAccount"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  account_keys_api_v1_service_accounts__account_id__api_keys_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ApiKey_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_account_key_api_v1_service_accounts__account_id__api_keys_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateKeyRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CreatedKey"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_threads_api_v1_sessions__session_id__threads_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_skill_revision_api_v1_skill_revisions__skill_revision_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        skill_revision_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillRevision"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_skill_revision_content_api_v1_skill_revisions__skill_revision_id__content_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        skill_revision_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_skill_upload_api_v1_skill_uploads__upload_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        upload_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillUploadReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_skill_upload_api_v1_skill_uploads__upload_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        upload_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_skill_api_v1_skills__skill_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Skill"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_skill_api_v1_skills__skill_id__delete: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_skill_api_v1_skills__skill_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateSkillRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Skill"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_skill_references_api_v1_skills__skill_id__references_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillAgentReferenceCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_skill_revisions_api_v1_skills__skill_id__revisions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillRevisionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_skill_revision_api_v1_skills__skill_id__revisions_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateSkillRevisionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillPublicationReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_thread_api_v1_threads__thread_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadResource"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_queued_submissions_api_v1_threads__thread_id__queued_submissions_get: {
    parameters: {
      query?: {
        state?: components["schemas"]["QueuedSubmissionState"];
        limit?: number;
      };
      header?: never;
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmissionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  consume_queued_submission_api_v1_threads__thread_id__queued_submissions_consume_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConsumeQueuedSubmissionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmissionConsumptionReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  reorder_queued_submissions_api_v1_threads__thread_id__queued_submissions_reorder_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReorderQueuedSubmissionsRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadQueueMutationReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_thread_runs_api_v1_threads__thread_id__runs_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  submit_thread_run_api_v1_threads__thread_id__runs_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ThreadRunSubmissionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadRunSubmissionReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  current_user_api_v1_users_me_get: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
    };
  };
  update_profile_api_v1_users_me_patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateProfileRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  sessions_api_v1_users_me_auth_sessions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_AuthSession_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  revoke_session_api_v1_users_me_auth_sessions__session_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  put_avatar_api_v1_users_me_avatar_put: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/octet-stream": Binary;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_avatar_api_v1_users_me_avatar_delete: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  request_email_change_api_v1_users_me_email_change_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EmailChangeRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  complete_email_change_api_v1_users_me_email_change_complete_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CompleteEmailChangeRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  change_password_api_v1_users_me_password_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ChangePasswordRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  personal_security_activity_api_v1_users_me_security_activity_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_SecurityEvent_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_avatar_api_v1_users__user_id__avatar__image_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        user_id: string;
        image_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspace_api_v1_workspaces__workspace__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_workspace_api_v1_workspaces__workspace__delete: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_workspace_api_v1_workspaces__workspace__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateResourceProfileRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_agents_api_v1_workspaces__workspace__agents_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        enabled?: boolean | null;
        source?: components["schemas"]["AgentSource"] | null;
        include_archived?: boolean;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_agent_api_v1_workspaces__workspace__agents_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateAgentRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCreateResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_agent_api_v1_workspaces__workspace__agents__agent__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
        agent: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_agent_api_v1_workspaces__workspace__agents__agent__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        workspace: string;
        agent: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateAgentRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  duplicate_agent_api_v1_workspaces__workspace__agents__agent__duplicate_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
        agent: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DuplicateAgentRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_agent_revisions_api_v1_workspaces__workspace__agents__agent__revisions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
        agent: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_agent_revision_api_v1_workspaces__workspace__agents__agent__revisions_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
        agent: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateAgentRevisionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCreateResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  restore_agent_revision_api_v1_workspaces__workspace__agents__agent__revisions__revision_id__restore_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        revision_id: string;
        workspace: string;
        agent: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RestoreAgentRevisionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCreateResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  change_agent_lifecycle_api_v1_workspaces__workspace__agents__agent___action__post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        "If-Match": string;
      };
      path: {
        action: "enable" | "disable" | "archive" | "unarchive";
        workspace: string;
        agent: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspace_member_keys_api_v1_workspaces__workspace__api_keys_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ApiKey_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  account_provider_types_api_v1_workspaces__workspace__application_account_provider_types_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountProviderDefinitionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_accounts_api_v1_workspaces__workspace__application_accounts_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_account_api_v1_workspaces__workspace__application_accounts_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateAccountRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_assets_api_v1_workspaces__workspace__assets_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        source_kind?: components["schemas"]["AssetSourceKind"] | null;
        source_run_id?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  upload_asset_api_v1_workspaces__workspace__assets_post: {
    parameters: {
      query: {
        filename: string;
        media_type?: string | null;
      };
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/octet-stream": Binary;
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Asset"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_connector_connections_api_v1_workspaces__workspace__connector_connections_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorConnectionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_connector_connection_api_v1_workspaces__workspace__connector_connections_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateConnectorConnectionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_connector_providers_api_v1_workspaces__workspace__connector_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_connector_provider_api_v1_workspaces__workspace__connector_providers_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateConnectorProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_providers_api_v1_workspaces__workspace__environment_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentProvider_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_provider_api_v1_workspaces__workspace__environment_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_templates_api_v1_workspaces__workspace__environment_templates_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentTemplate_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_template_api_v1_workspaces__workspace__environment_templates_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateTemplateRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_environments_api_v1_workspaces__workspace__environments_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_Environment_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_environment_api_v1_workspaces__workspace__environments_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json":
          | components["schemas"]["NewEnvironmentSelection"]
          | components["schemas"]["RegisterEnvironmentRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Environment"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_workspace_events_api_v1_workspaces__workspace__events_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WorkspaceEventPage"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_hook_subscriptions_api_v1_workspaces__workspace__hook_subscriptions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscriptionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_hook_subscription_api_v1_workspaces__workspace__hook_subscriptions_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateHookSubscriptionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  put_workspace_icon_api_v1_workspaces__workspace__icon_put: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/octet-stream": Binary;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  delete_workspace_icon_api_v1_workspaces__workspace__icon_delete: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_workspace_icon_api_v1_workspaces__workspace__icon__image_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        image_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspace_invitations_api_v1_workspaces__workspace__invitations_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Invitation_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  invite_to_workspace_api_v1_workspaces__workspace__invitations_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["InviteWorkspaceRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationDelivery"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_mcp_connections_api_v1_workspaces__workspace__mcp_connections_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnectionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_mcp_connection_api_v1_workspaces__workspace__mcp_connections_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateMCPConnectionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPConnection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspace_members_api_v1_workspaces__workspace__members_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_User_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_model_providers_api_v1_workspaces__workspace__model_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        name?: string | null;
        provider_type?: string | null;
        enabled?: boolean | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProviderCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_model_provider_api_v1_workspaces__workspace__model_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateModelProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_model_provider_api_v1_workspaces__workspace__model_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_model_provider_api_v1_workspaces__workspace__model_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateModelProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  describe_provider_model_api_v1_workspaces__workspace__model_providers__provider_id__describe_model_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DescribeModelRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelDescription"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  discover_provider_models_api_v1_workspaces__workspace__model_providers__provider_id__discover_models_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelDiscovery"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  test_model_provider_api_v1_workspaces__workspace__model_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_models_api_v1_workspaces__workspace__models_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        query?: string | null;
        provider_id?: string | null;
        enabled?: boolean | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_model_api_v1_workspaces__workspace__models_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateModelRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_model_api_v1_workspaces__workspace__models__model_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        model_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_model_api_v1_workspaces__workspace__models__model_id__patch: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        model_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateModelRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  test_model_api_v1_workspaces__workspace__models__model_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        model_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: {
      content: {
        "application/json": components["schemas"]["ModelTestRequest"] | null;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspace_permissions_api_v1_workspaces__workspace__permissions_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Permissions"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  personal_keys_api_v1_workspaces__workspace__personal_api_keys_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ApiKey_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_personal_key_api_v1_workspaces__workspace__personal_api_keys_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateKeyRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CreatedKey"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspace_roles_api_v1_workspaces__workspace__role_bindings_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_RoleBinding_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  add_workspace_member_api_v1_workspaces__workspace__role_bindings_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SetRoleRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_workspace_runs_api_v1_workspaces__workspace__runs_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  start_run_api_v1_workspaces__workspace__runs_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["StartRunRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_workspace_provider_api_v1_workspaces__workspace__search_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        type?: string | null;
        enabled?: boolean | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_workspace_provider_api_v1_workspaces__workspace__search_providers_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateSearchProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  update_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header?: {
        "If-Match"?: string | null;
      };
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateSearchProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProvider"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  references_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__references_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderReferenceCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  test_workspace_provider_api_v1_workspaces__workspace__search_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SearchConfiguration"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SearchProviderTestResult"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  workspace_security_events_api_v1_workspaces__workspace__security_audit_events_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_SecurityEvent_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  accounts_api_v1_workspaces__workspace__service_accounts_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ServiceAccount_"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_account_api_v1_workspaces__workspace__service_accounts_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateServiceAccountRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ServiceAccount"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_sessions_api_v1_workspaces__workspace__sessions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  stage_skill_upload_api_v1_workspaces__workspace__skill_uploads_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/zip": Binary;
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillUploadReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_skills_api_v1_workspaces__workspace__skills_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_skill_api_v1_workspaces__workspace__skills_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateSkillRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillPublicationReceipt"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  create_thread_api_v1_workspaces__workspace__threads_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateThreadRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Thread"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  list_traces_api_v1_workspaces__workspace__traces_get: {
    parameters: {
      query?: {
        from?: string | null;
        to?: string | null;
        limit?: number;
        cursor?: string | null;
        query?: string | null;
        search_in?: components["schemas"]["SearchIn"] | null;
        thread_id?: string | null;
        run_id?: string | null;
        run_attempt_id?: string | null;
      };
      header?: never;
      path: {
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TraceCollection"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
  get_trace_api_v1_workspaces__workspace__traces__trace_id__get: {
    parameters: {
      query?: {
        view?: components["schemas"]["TraceView"];
      };
      header?: never;
      path: {
        trace_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TraceDetail"];
        };
      };
      /** @description Validation Error */
      422: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HTTPValidationError"];
        };
      };
    };
  };
}

export type Binary = Blob | ReadableStream<Uint8Array>;
