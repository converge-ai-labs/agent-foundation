export interface paths {
  "/api/v1/agent-revisions/{agent_revision_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Agent Revision */
    get: operations["get_agent_revisions_agent_revision_id"];
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
    get: operations["get_api_keys_key_id"];
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
    post: operations["post_api_keys_key_id_revoke"];
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
    get: operations["get_application_accounts_account_id"];
    put?: never;
    post?: never;
    /** Delete Account */
    delete: operations["delete_application_accounts_account_id"];
    options?: never;
    head?: never;
    /** Update Account */
    patch: operations["patch_application_accounts_account_id"];
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/activate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Activate Bot */
    post: operations["post_application_accounts_account_id_bot_activate"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/checks": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Check Bot */
    post: operations["post_application_accounts_account_id_bot_checks"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/checks/latest": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Latest Bot Check */
    get: operations["get_application_accounts_account_id_bot_checks_latest"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/conversations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Discover Bot Conversations */
    get: operations["get_application_accounts_account_id_bot_conversations"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/memory-settings": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Memory Settings */
    get: operations["get_application_accounts_account_id_bot_memory_settings"];
    /** Update Memory Settings */
    put: operations["put_application_accounts_account_id_bot_memory_settings"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/replies": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Bot Reply Observations */
    get: operations["get_application_accounts_account_id_bot_replies"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/setup": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Bot Setup */
    get: operations["get_application_accounts_account_id_bot_setup"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/summary": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Bot Summary */
    get: operations["get_application_accounts_account_id_bot_summary"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/tests": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Create Bot Setup Test */
    post: operations["post_application_accounts_account_id_bot_tests"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/tests/latest": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Latest Bot Setup Test */
    get: operations["get_application_accounts_account_id_bot_tests_latest"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/tests/{test_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Bot Setup Test */
    get: operations["get_application_accounts_account_id_bot_tests_test_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/bot/threads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Bot Conversation Threads */
    get: operations["get_application_accounts_account_id_bot_threads"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    put: operations["put_application_accounts_account_id_credentials"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/event-connection": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Event Connection */
    get: operations["get_application_accounts_account_id_event_connection"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Scopes */
    get: operations["get_application_accounts_account_id_memory_scopes"];
    put?: never;
    /** Configure */
    post: operations["post_application_accounts_account_id_memory_scopes"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Documents */
    get: operations["get_application_accounts_account_id_memory_scopes_scope_id_documents"];
    put?: never;
    /** Add */
    post: operations["post_application_accounts_account_id_memory_scopes_scope_id_documents"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents/search": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Search */
    post: operations["post_application_accounts_account_id_memory_scopes_scope_id_documents_search"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/documents/{document_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get */
    get: operations["get_application_accounts_account_id_memory_scopes_scope_id_documents_document_id"];
    put?: never;
    post?: never;
    /** Remove */
    delete: operations["delete_application_accounts_account_id_memory_scopes_scope_id_documents_document_id"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/index": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Index */
    get: operations["get_application_accounts_account_id_memory_scopes_scope_id_index"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Operations */
    get: operations["get_application_accounts_account_id_memory_scopes_scope_id_operations"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations/{document_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Operation Status */
    get: operations["get_application_accounts_account_id_memory_scopes_scope_id_operations_document_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/application-accounts/{account_id}/memory-scopes/{scope_id}/operations/{document_id}/reconcile": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Reconcile Operation */
    post: operations["post_application_accounts_account_id_memory_scopes_scope_id_operations_document_id_reconcile"];
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
    get: operations["get_application_accounts_account_id_targets"];
    put?: never;
    /** Create */
    post: operations["post_application_accounts_account_id_targets"];
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
    get: operations["get_application_accounts_account_id_targets_target_id"];
    /** Replace */
    put: operations["put_application_accounts_account_id_targets_target_id"];
    post?: never;
    /** Delete */
    delete: operations["delete_application_accounts_account_id_targets_target_id"];
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
    post: operations["post_application_accounts_account_id_action"];
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
    get: operations["get_assets_asset_id"];
    put?: never;
    post?: never;
    /** Delete Asset */
    delete: operations["delete_assets_asset_id"];
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
    get: operations["get_assets_asset_id_content"];
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
    get: operations["get_auth_configuration"];
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
    get: operations["get_auth_context"];
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
    get: operations["get_auth_csrf"];
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
    post: operations["post_auth_login"];
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
    post: operations["post_auth_logout"];
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
    post: operations["post_auth_password_reset"];
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
    post: operations["post_auth_password_reset_complete"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-drafts/{draft_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Draft */
    get: operations["get_configuration_drafts_draft_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Draft */
    patch: operations["patch_configuration_drafts_draft_id"];
    trace?: never;
  };
  "/api/v1/configuration-drafts/{draft_id}/applications": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Applications */
    get: operations["get_configuration_drafts_draft_id_applications"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-drafts/{draft_id}/apply": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Apply Draft */
    post: operations["post_configuration_drafts_draft_id_apply"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-drafts/{draft_id}/discard": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discard Draft */
    post: operations["post_configuration_drafts_draft_id_discard"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-drafts/{draft_id}/rebase": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Rebase Draft */
    post: operations["post_configuration_drafts_draft_id_rebase"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-sessions/{session_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Session */
    get: operations["get_configuration_sessions_session_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-sessions/{session_id}/threads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Threads */
    get: operations["get_configuration_sessions_session_id_threads"];
    put?: never;
    /** Create Thread */
    post: operations["post_configuration_sessions_session_id_threads"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-threads/{thread_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Thread */
    get: operations["get_configuration_threads_thread_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/configuration-threads/{thread_id}/inputs": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Submit Input */
    post: operations["post_configuration_threads_thread_id_inputs"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connection-authorizations/{authorization_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Connection Authorization */
    get: operations["get_connection_authorizations_authorization_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connection-authorizations/{authorization_id}/cancel": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Cancel Connection Authorization */
    post: operations["post_connection_authorizations_authorization_id_cancel"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connection-authorizations/{authorization_id}/complete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Complete Connection Authorization */
    post: operations["post_connection_authorizations_authorization_id_complete"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connection-authorizations/{authorization_id}/launch": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Launch Connection Authorization */
    post: operations["post_connection_authorizations_authorization_id_launch"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connection-authorizations/{authorization_id}/receive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Receive Connection Authorization */
    post: operations["post_connection_authorizations_authorization_id_receive"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Connection */
    get: operations["get_connections_connection_id"];
    put?: never;
    post?: never;
    /** Delete Connection */
    delete: operations["delete_connections_connection_id"];
    options?: never;
    head?: never;
    /** Update Connection */
    patch: operations["patch_connections_connection_id"];
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/authorizations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Create Connection Authorization */
    post: operations["post_connections_connection_id_authorizations"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/check": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Check Connection */
    post: operations["post_connections_connection_id_check"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/connector/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Connector Authorization */
    post: operations["post_connections_connection_id_connector_revoke"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/disable": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Disable Connection */
    post: operations["post_connections_connection_id_disable"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/enable": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Enable Connection */
    post: operations["post_connections_connection_id_enable"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/mcp/discover": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discover Mcp Tools */
    post: operations["post_connections_connection_id_mcp_discover"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/mcp/oauth-client": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Mcp Oauth Client */
    get: operations["get_connections_connection_id_mcp_oauth_client"];
    /** Configure Mcp Oauth Client */
    put: operations["put_connections_connection_id_mcp_oauth_client"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/mcp/oauth-discovery": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discover Mcp Oauth */
    post: operations["post_connections_connection_id_mcp_oauth_discovery"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/mcp/oauth-setup": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Get Mcp Oauth Setup */
    post: operations["post_connections_connection_id_mcp_oauth_setup"];
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
    get: operations["get_connector_provider_types"];
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
    get: operations["get_connector_providers_connector_provider_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Connector Provider */
    patch: operations["patch_connector_providers_connector_provider_id"];
    trace?: never;
  };
  "/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Connector */
    get: operations["get_connector_providers_connector_provider_id_connectors_connector_key"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    get: operations["get_connector_providers_connector_provider_id_connectors_connector_key_tools"];
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
    post: operations["post_connector_providers_connector_provider_id_credentials"];
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
    post: operations["post_connector_providers_connector_provider_id_discover_connectors"];
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
    post: operations["post_connector_providers_connector_provider_id_test"];
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
    post: operations["post_connector_providers_connector_provider_id_action"];
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
    get: operations["get_environment_commands_command_id"];
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
    get: operations["get_environment_provider_types"];
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
    get: operations["get_environment_provider_types_provider_type"];
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
    patch: operations["patch_environment_providers_provider_id"];
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
    put: operations["put_environment_providers_provider_id_credential"];
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
    get: operations["get_environment_providers_resource_id"];
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
    get: operations["get_environment_template_revisions_revision_id"];
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
    get: operations["get_environment_templates_resource_id"];
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
    patch: operations["patch_environment_templates_template_id"];
    trace?: never;
  };
  "/api/v1/environment-templates/{template_id}/labels": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Template Labels */
    get: operations["get_environment_templates_template_id_labels"];
    /** Put Template Labels */
    put: operations["put_environment_templates_template_id_labels"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    get: operations["get_environment_templates_template_id_revisions"];
    put?: never;
    /** Create Revision */
    post: operations["post_environment_templates_template_id_revisions"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}": {
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
    /** Update Environment */
    patch: operations["patch_environments_environment_id"];
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/connection": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Connection Status */
    get: operations["get_environments_environment_id_connection"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/connection-tickets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Issue Ticket */
    post: operations["post_environments_environment_id_connection_tickets"];
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
    post: operations["post_environments_environment_id_delete"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/labels": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Environment Labels */
    get: operations["get_environments_environment_id_labels"];
    /** Put Environment Labels */
    put: operations["put_environments_environment_id_labels"];
    post?: never;
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
    post: operations["post_environments_environment_id_stop"];
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
    get: operations["get_environments_resource_id"];
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
    get: operations["get_hook_subscriptions_subscription_id"];
    /** Update Hook Subscription */
    put: operations["put_hook_subscriptions_subscription_id"];
    post?: never;
    /** Delete Hook Subscription */
    delete: operations["delete_hook_subscriptions_subscription_id"];
    options?: never;
    head?: never;
    /** Update Hook Subscription State */
    patch: operations["patch_hook_subscriptions_subscription_id"];
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
    post: operations["post_hook_subscriptions_subscription_id_deliveries_delivery_id_redrive"];
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
    post: operations["post_invitations_invitation_id_accept"];
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
    post: operations["post_invitations_invitation_id_resend"];
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
    post: operations["post_invitations_invitation_id_revoke"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/mcp-servers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Mcp Servers */
    get: operations["get_mcp_servers"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/mcp-servers/{server_key}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Mcp Server */
    get: operations["get_mcp_servers_server_key"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-provider-types": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Types */
    get: operations["get_memory_provider_types"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-provider-types/{provider_type}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Type */
    get: operations["get_memory_provider_types_provider_type"];
    put?: never;
    post?: never;
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
    get: operations["get_model_provider_types"];
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
    get: operations["get_organizations"];
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
    get: operations["get_organizations_organization"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Organization */
    patch: operations["patch_organizations_organization"];
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
    get: operations["get_organizations_organization_connector_providers"];
    put?: never;
    /** Organization Create Connector Provider */
    post: operations["post_organizations_organization_connector_providers"];
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
    get: operations["get_organizations_organization_environment_providers"];
    put?: never;
    /** Organization Create Provider */
    post: operations["post_organizations_organization_environment_providers"];
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
    get: operations["get_organizations_organization_environment_templates"];
    put?: never;
    /** Organization Create Template */
    post: operations["post_organizations_organization_environment_templates"];
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
    put: operations["put_organizations_organization_icon"];
    post?: never;
    /** Delete Organization Icon */
    delete: operations["delete_organizations_organization_icon"];
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
    get: operations["get_organizations_organization_icon_image_id"];
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
    get: operations["get_organizations_organization_invitations"];
    put?: never;
    /** Invite */
    post: operations["post_organizations_organization_invitations"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/memory-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organization Provider */
    get: operations["get_organizations_organization_memory_providers"];
    put?: never;
    /** Create Organization Provider */
    post: operations["post_organizations_organization_memory_providers"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/memory-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Organization Provider */
    get: operations["get_organizations_organization_memory_providers_provider_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Organization Provider */
    patch: operations["patch_organizations_organization_memory_providers_provider_id"];
    trace?: never;
  };
  "/api/v1/organizations/{organization}/memory-providers/{provider_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** References Organization Provider */
    get: operations["get_organizations_organization_memory_providers_provider_id_references"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/model-catalog": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Organization List Model Catalog */
    get: operations["get_organizations_organization_model_catalog"];
    put?: never;
    post?: never;
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
    get: operations["get_organizations_organization_model_providers"];
    put?: never;
    /** Organization Create Model Provider */
    post: operations["post_organizations_organization_model_providers"];
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
    get: operations["get_organizations_organization_model_providers_provider_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Organization Update Model Provider */
    patch: operations["patch_organizations_organization_model_providers_provider_id"];
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
    post: operations["post_organizations_organization_model_providers_provider_id_test"];
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
    get: operations["get_organizations_organization_models"];
    put?: never;
    /** Organization Create Model */
    post: operations["post_organizations_organization_models"];
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
    get: operations["get_organizations_organization_models_model_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Organization Update Model */
    patch: operations["patch_organizations_organization_models_model_id"];
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
    post: operations["post_organizations_organization_models_model_id_test"];
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
    get: operations["get_organizations_organization_permissions"];
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
    get: operations["get_organizations_organization_role_bindings"];
    put?: never;
    /** Create Organization Binding */
    post: operations["post_organizations_organization_role_bindings"];
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
    get: operations["get_organizations_organization_security_audit_events"];
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
    get: operations["get_organizations_organization_users"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/web-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organization Provider */
    get: operations["get_organizations_organization_web_providers"];
    put?: never;
    /** Create Organization Provider */
    post: operations["post_organizations_organization_web_providers"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/web-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Organization Provider */
    get: operations["get_organizations_organization_web_providers_provider_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Organization Provider */
    patch: operations["patch_organizations_organization_web_providers_provider_id"];
    trace?: never;
  };
  "/api/v1/organizations/{organization}/web-providers/{provider_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** References Organization Provider */
    get: operations["get_organizations_organization_web_providers_provider_id_references"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization}/web-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Organization Provider */
    post: operations["post_organizations_organization_web_providers_provider_id_test"];
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
    get: operations["get_organizations_organization_workspaces"];
    put?: never;
    /** Create Workspace */
    post: operations["post_organizations_organization_workspaces"];
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
    get: operations["get_queued_submissions_queued_submission_id"];
    put?: never;
    post?: never;
    /** Delete Queued Submission */
    delete: operations["delete_queued_submissions_queued_submission_id"];
    options?: never;
    head?: never;
    /** Update Queued Submission */
    patch: operations["patch_queued_submissions_queued_submission_id"];
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
    get: operations["get_role_bindings_binding_id"];
    put?: never;
    post?: never;
    /** Remove Member */
    delete: operations["delete_role_bindings_binding_id"];
    options?: never;
    head?: never;
    /** Change Role */
    patch: operations["patch_role_bindings_binding_id"];
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
    get: operations["get_run_attempts_run_attempt_id"];
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
    get: operations["get_run_attempts_run_attempt_id_events"];
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
    get: operations["get_runs_run_id"];
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
    get: operations["get_runs_run_id_attempts"];
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
    get: operations["get_runs_run_id_events"];
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
    post: operations["post_runs_run_id_feedback"];
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
    post: operations["post_runs_run_id_fork"];
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
    post: operations["post_runs_run_id_interrupt"];
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
    get: operations["get_runs_run_id_items"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/labels": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Run Labels */
    get: operations["get_runs_run_id_labels"];
    /** Put Run Labels */
    put: operations["put_runs_run_id_labels"];
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
    get: operations["get_runs_run_id_lineage"];
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
    get: operations["get_runs_run_id_pending_actions"];
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
    post: operations["post_runs_run_id_retry"];
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
    post: operations["post_runs_run_id_steer"];
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
    get: operations["get_runs_run_id_steers_steer_id"];
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
    get: operations["get_runs_run_id_stream"];
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
    post: operations["post_runs_source_run_id_continue"];
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
    get: operations["get_service_accounts_account_id"];
    put?: never;
    post?: never;
    /** Delete Account */
    delete: operations["delete_service_accounts_account_id"];
    options?: never;
    head?: never;
    /** Update Account */
    patch: operations["patch_service_accounts_account_id"];
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
    get: operations["get_service_accounts_account_id_api_keys"];
    put?: never;
    /** Create Account Key */
    post: operations["post_service_accounts_account_id_api_keys"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/sessions/{session_id}/labels": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Session Labels */
    get: operations["get_sessions_session_id_labels"];
    /** Put Session Labels */
    put: operations["put_sessions_session_id_labels"];
    post?: never;
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
    get: operations["get_sessions_session_id_threads"];
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
    get: operations["get_skill_revisions_skill_revision_id"];
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
    get: operations["get_skill_revisions_skill_revision_id_content"];
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
    get: operations["get_skill_uploads_upload_id"];
    put?: never;
    post?: never;
    /** Delete Skill Upload */
    delete: operations["delete_skill_uploads_upload_id"];
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
    get: operations["get_skills_skill_id"];
    put?: never;
    post?: never;
    /** Delete Skill */
    delete: operations["delete_skills_skill_id"];
    options?: never;
    head?: never;
    /** Update Skill */
    patch: operations["patch_skills_skill_id"];
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/labels": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Skill Labels */
    get: operations["get_skills_skill_id_labels"];
    /** Put Skill Labels */
    put: operations["put_skills_skill_id_labels"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    get: operations["get_skills_skill_id_references"];
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
    get: operations["get_skills_skill_id_revisions"];
    put?: never;
    /** Create Skill Revision */
    post: operations["post_skills_skill_id_revisions"];
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
    get: operations["get_threads_thread_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/labels": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Thread Labels */
    get: operations["get_threads_thread_id_labels"];
    /** Put Thread Labels */
    put: operations["put_threads_thread_id_labels"];
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
    get: operations["get_threads_thread_id_queued_submissions"];
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
    post: operations["post_threads_thread_id_queued_submissions_consume"];
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
    post: operations["post_threads_thread_id_queued_submissions_reorder"];
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
    get: operations["get_threads_thread_id_runs"];
    put?: never;
    /** Submit Thread Run */
    post: operations["post_threads_thread_id_runs"];
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
    get: operations["get_users_me"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Profile */
    patch: operations["patch_users_me"];
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
    get: operations["get_users_me_auth_sessions"];
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
    delete: operations["delete_users_me_auth_sessions_session_id"];
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
    put: operations["put_users_me_avatar"];
    post?: never;
    /** Delete Avatar */
    delete: operations["delete_users_me_avatar"];
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
    post: operations["post_users_me_email_change"];
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
    post: operations["post_users_me_email_change_complete"];
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
    post: operations["post_users_me_password"];
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
    get: operations["get_users_me_security_activity"];
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
    get: operations["get_users_user_id_avatar_image_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/web-provider-types": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Types */
    get: operations["get_web_provider_types"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/web-provider-types/{provider_type}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Type */
    get: operations["get_web_provider_types_provider_type"];
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
    get: operations["get_workspaces_workspace"];
    put?: never;
    post?: never;
    /** Delete Workspace */
    delete: operations["delete_workspaces_workspace"];
    options?: never;
    head?: never;
    /** Update Workspace */
    patch: operations["patch_workspaces_workspace"];
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
    get: operations["get_workspaces_workspace_agents"];
    put?: never;
    /** Create Agent */
    post: operations["post_workspaces_workspace_agents"];
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
    get: operations["get_workspaces_workspace_agents_agent"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Agent */
    patch: operations["patch_workspaces_workspace_agents_agent"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}/avatar": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Put Agent Avatar */
    put: operations["put_workspaces_workspace_agents_agent_avatar"];
    post?: never;
    /** Delete Agent Avatar */
    delete: operations["delete_workspaces_workspace_agents_agent_avatar"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}/avatar/{image_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Agent Avatar */
    get: operations["get_workspaces_workspace_agents_agent_avatar_image_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    post: operations["post_workspaces_workspace_agents_agent_duplicate"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/agents/{agent}/labels": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Agent Labels */
    get: operations["get_workspaces_workspace_agents_agent_labels"];
    /** Put Agent Labels */
    put: operations["put_workspaces_workspace_agents_agent_labels"];
    post?: never;
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
    get: operations["get_workspaces_workspace_agents_agent_revisions"];
    put?: never;
    /** Create Agent Revision */
    post: operations["post_workspaces_workspace_agents_agent_revisions"];
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
    post: operations["post_workspaces_workspace_agents_agent_revisions_revision_id_restore"];
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
    post: operations["post_workspaces_workspace_agents_agent_action"];
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
    get: operations["get_workspaces_workspace_api_keys"];
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
    get: operations["get_workspaces_workspace_application_account_provider_types"];
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
    get: operations["get_workspaces_workspace_application_accounts"];
    put?: never;
    /** Create Account */
    post: operations["post_workspaces_workspace_application_accounts"];
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
    get: operations["get_workspaces_workspace_assets"];
    put?: never;
    /** Upload Asset */
    post: operations["post_workspaces_workspace_assets"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/bots": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Bot Collection */
    get: operations["get_workspaces_workspace_bots"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/bots/feishu/installation": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Discover Feishu Installation */
    post: operations["post_workspaces_workspace_bots_feishu_installation"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/configuration-assistant/readiness": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Readiness */
    get: operations["get_workspaces_workspace_configuration_assistant_readiness"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/configuration-sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Sessions */
    get: operations["get_workspaces_workspace_configuration_sessions"];
    put?: never;
    /** Create Session */
    post: operations["post_workspaces_workspace_configuration_sessions"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/connections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Connections */
    get: operations["get_workspaces_workspace_connections"];
    put?: never;
    /** Create Connection */
    post: operations["post_workspaces_workspace_connections"];
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
    get: operations["get_workspaces_workspace_connector_providers"];
    put?: never;
    /** Create Connector Provider */
    post: operations["post_workspaces_workspace_connector_providers"];
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
    get: operations["get_workspaces_workspace_environment_providers"];
    put?: never;
    /** Create Provider */
    post: operations["post_workspaces_workspace_environment_providers"];
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
    get: operations["get_workspaces_workspace_environment_templates"];
    put?: never;
    /** Create Template */
    post: operations["post_workspaces_workspace_environment_templates"];
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
    get: operations["get_workspaces_workspace_environments"];
    put?: never;
    /** Create Environment */
    post: operations["post_workspaces_workspace_environments"];
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
    get: operations["get_workspaces_workspace_events"];
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
    get: operations["get_workspaces_workspace_hook_subscriptions"];
    put?: never;
    /** Create Hook Subscription */
    post: operations["post_workspaces_workspace_hook_subscriptions"];
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
    put: operations["put_workspaces_workspace_icon"];
    post?: never;
    /** Delete Workspace Icon */
    delete: operations["delete_workspaces_workspace_icon"];
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
    get: operations["get_workspaces_workspace_icon_image_id"];
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
    get: operations["get_workspaces_workspace_invitations"];
    put?: never;
    /** Invite To Workspace */
    post: operations["post_workspaces_workspace_invitations"];
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
    get: operations["get_workspaces_workspace_members"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/memory-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Provider */
    get: operations["get_workspaces_workspace_memory_providers"];
    put?: never;
    /** Create Workspace Provider */
    post: operations["post_workspaces_workspace_memory_providers"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Workspace Provider */
    get: operations["get_workspaces_workspace_memory_providers_provider_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Workspace Provider */
    patch: operations["patch_workspaces_workspace_memory_providers_provider_id"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Memories */
    get: operations["get_workspaces_workspace_memory_providers_provider_id_memories"];
    put?: never;
    /** Add Memory */
    post: operations["post_workspaces_workspace_memory_providers_provider_id_memories"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories/search": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Search Memories */
    post: operations["post_workspaces_workspace_memory_providers_provider_id_memories_search"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memories/{memory_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Memory */
    get: operations["get_workspaces_workspace_memory_providers_provider_id_memories_memory_id"];
    /** Update Memory */
    put: operations["put_workspaces_workspace_memory_providers_provider_id_memories_memory_id"];
    post?: never;
    /** Delete Memory */
    delete: operations["delete_workspaces_workspace_memory_providers_provider_id_memories_memory_id"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/memory-access": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Memory Access */
    get: operations["get_workspaces_workspace_memory_providers_provider_id_memory_access"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/memory-providers/{provider_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** References Workspace Provider */
    get: operations["get_workspaces_workspace_memory_providers_provider_id_references"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/model-catalog": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Model Catalog */
    get: operations["get_workspaces_workspace_model_catalog"];
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
    get: operations["get_workspaces_workspace_model_providers"];
    put?: never;
    /** Create Model Provider */
    post: operations["post_workspaces_workspace_model_providers"];
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
    get: operations["get_workspaces_workspace_model_providers_provider_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Model Provider */
    patch: operations["patch_workspaces_workspace_model_providers_provider_id"];
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
    post: operations["post_workspaces_workspace_model_providers_provider_id_test"];
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
    get: operations["get_workspaces_workspace_models"];
    put?: never;
    /** Create Model */
    post: operations["post_workspaces_workspace_models"];
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
    get: operations["get_workspaces_workspace_models_model_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Model */
    patch: operations["patch_workspaces_workspace_models_model_id"];
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
    post: operations["post_workspaces_workspace_models_model_id_test"];
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
    get: operations["get_workspaces_workspace_permissions"];
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
    get: operations["get_workspaces_workspace_personal_api_keys"];
    put?: never;
    /** Create Personal Key */
    post: operations["post_workspaces_workspace_personal_api_keys"];
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
    get: operations["get_workspaces_workspace_role_bindings"];
    put?: never;
    /** Add Workspace Member */
    post: operations["post_workspaces_workspace_role_bindings"];
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
    get: operations["get_workspaces_workspace_runs"];
    put?: never;
    /** Start Run */
    post: operations["post_workspaces_workspace_runs"];
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
    get: operations["get_workspaces_workspace_security_audit_events"];
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
    get: operations["get_workspaces_workspace_service_accounts"];
    put?: never;
    /** Create Account */
    post: operations["post_workspaces_workspace_service_accounts"];
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
    get: operations["get_workspaces_workspace_sessions"];
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
    post: operations["post_workspaces_workspace_skill_uploads"];
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
    get: operations["get_workspaces_workspace_skills"];
    put?: never;
    /** Create Skill */
    post: operations["post_workspaces_workspace_skills"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/skills/{skill_key}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Skill By Key */
    get: operations["get_workspaces_workspace_skills_skill_key"];
    put?: never;
    post?: never;
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
    post: operations["post_workspaces_workspace_threads"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/toolsets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Toolsets */
    get: operations["get_workspaces_workspace_toolsets"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/toolsets/validate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Validate Toolsets */
    post: operations["post_workspaces_workspace_toolsets_validate"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/trace-query": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Trace Query */
    get: operations["get_workspaces_workspace_trace_query"];
    put?: never;
    post?: never;
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
    get: operations["get_workspaces_workspace_traces"];
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
    get: operations["get_workspaces_workspace_traces_trace_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/traces/{trace_id}/observations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Trace Observations */
    get: operations["get_workspaces_workspace_traces_trace_id_observations"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/web-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Provider */
    get: operations["get_workspaces_workspace_web_providers"];
    put?: never;
    /** Create Workspace Provider */
    post: operations["post_workspaces_workspace_web_providers"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/web-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Workspace Provider */
    get: operations["get_workspaces_workspace_web_providers_provider_id"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Workspace Provider */
    patch: operations["patch_workspaces_workspace_web_providers_provider_id"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/web-providers/{provider_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** References Workspace Provider */
    get: operations["get_workspaces_workspace_web_providers_provider_id_references"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace}/web-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Workspace Provider */
    post: operations["post_workspaces_workspace_web_providers_provider_id_test"];
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
      /** @default all_accessible */
      reception_scope?: components["schemas"]["ReceptionScope"];
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
    /** AccountMemorySettings */
    AccountMemorySettings: {
      /** Account Id */
      account_id: string;
      memory: components["schemas"]["MemorySettings"] | null;
      /** Version */
      version: number;
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
    /** ActivateBotRequest */
    ActivateBotRequest: {
      /** Agent Id */
      agent_id: string;
      /** Conversation Id */
      conversation_id: string;
      /** Execution Service Account Id */
      execution_service_account_id: string;
      /** Expected Version */
      expected_version: number;
      policy: components["schemas"]["MessagingPolicy"];
      /** Target Id */
      target_id: string;
      /** Target Version */
      target_version: number;
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
    /** ActorRef */
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
      current_revision_id: string | null;
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
      /** Image Url */
      image_url?: string | null;
      /** Key */
      key: string;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      source: components["schemas"]["AgentSource"];
      /** System Purpose */
      system_purpose?: "configuration_assistant" | null;
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
    /** AgentConfig-Input */
    "AgentConfig-Input": {
      /**
       * Client Tools
       * @default []
       */
      client_tools?: components["schemas"]["ClientToolDefinition"][];
      /**
       * Connection Tools
       * @default []
       */
      connection_tools?: components["schemas"]["ConnectionToolSelection"][];
      input_adapter: components["schemas"]["InputAdapterConfig"];
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      memory?: components["schemas"]["MemorySelection"] | null;
      model: components["schemas"]["AgentModel"];
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /**
       * Plugins
       * @default []
       */
      plugins?: components["schemas"]["PluginSelection"][];
      protocol: components["schemas"]["ProtocolConfig"];
      retries?: components["schemas"]["RetryConfig"] | null;
      reviewer?: components["schemas"]["AgentReviewer"] | null;
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
      /** Toolsets */
      toolsets?: {
        [key: string]: components["schemas"]["ToolsetSelection"];
      };
    };
    /** AgentConfig-Output */
    "AgentConfig-Output": {
      /**
       * Client Tools
       * @default []
       */
      client_tools?: components["schemas"]["ClientToolDefinition"][];
      /**
       * Connection Tools
       * @default []
       */
      connection_tools?: components["schemas"]["ConnectionToolSelection"][];
      input_adapter: components["schemas"]["InputAdapterConfig"];
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      memory?: components["schemas"]["MemorySelection"] | null;
      model: components["schemas"]["AgentModel"];
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /**
       * Plugins
       * @default []
       */
      plugins?: components["schemas"]["PluginSelection"][];
      protocol: components["schemas"]["ProtocolConfig"];
      retries?: components["schemas"]["RetryConfig"] | null;
      reviewer?: components["schemas"]["AgentReviewer"] | null;
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
      /** Toolsets */
      toolsets?: {
        [key: string]: components["schemas"]["ToolsetSelection"];
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
      characteristics?: components["schemas"]["AgentModelCharacteristics"];
      /** Model Key */
      model_key: string;
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /**
     * AgentModelCharacteristics
     * @description Agent-owned context policy layered over Model declarations.
     */
    AgentModelCharacteristics: {
      /**
       * Compact Threshold
       * @default 0.9
       */
      compact_threshold?: number;
      /** Context Window Tokens */
      context_window_tokens?: number | null;
      /**
       * Proactive Context Management Threshold
       * @default 0.65
       */
      proactive_context_management_threshold?: number | null;
    };
    /**
     * AgentReviewer
     * @description Reviewer selected by immutable managed Model ID, never a provider route.
     */
    AgentReviewer: {
      /** Instruction */
      instruction?: string | null;
      /** Model */
      model: string;
      /** Model Settings */
      model_settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * On Error
       * @default approval_required
       * @enum {string}
       */
      on_error?: "deny" | "approval_required" | "allow";
      /**
       * On Flagged
       * @default deny
       * @enum {string}
       */
      on_flagged?: "deny" | "approval_required";
      /** @default extra_high */
      risk_threshold?: components["schemas"]["ToolRiskLevel"];
      /** Rules */
      rules?: {
        [key: string]: components["schemas"]["ToolReviewRule"];
      };
      /** Shell Instruction */
      shell_instruction?: string | null;
      /**
       * Timeout Seconds
       * @default 120
       */
      timeout_seconds?: number;
    };
    /** AgentRevision */
    AgentRevision: {
      /** Agent Id */
      agent_id: string;
      config: components["schemas"]["AgentConfig-Output"];
      /** Config Digest */
      config_digest: string;
      /**
       * Connection Tools
       * @default []
       */
      connection_tools?: components["schemas"]["ConnectionToolSelection"][];
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
    /** AgentRunOverride-Input */
    "AgentRunOverride-Input": {
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connection Tools */
      connection_tools?:
        components["schemas"]["ConnectionToolSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      memory?: components["schemas"]["MemorySelection"] | null;
      model?: components["schemas"]["ModelOverride"] | null;
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /** Plugins */
      plugins?: components["schemas"]["PluginSelection"][] | null;
      retries?: components["schemas"]["RetryOverride"] | null;
      reviewer?: components["schemas"]["AgentReviewer"] | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentOverride-Input"] | null;
      } | null;
      /** Toolsets */
      toolsets?: {
        [key: string]: components["schemas"]["ToolsetSelection"];
      } | null;
    };
    /** AgentRunOverride-Output */
    "AgentRunOverride-Output": {
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connection Tools */
      connection_tools?:
        components["schemas"]["ConnectionToolSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      memory?: components["schemas"]["MemorySelection"] | null;
      model?: components["schemas"]["ModelOverride"] | null;
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /** Plugins */
      plugins?: components["schemas"]["PluginSelection"][] | null;
      retries?: components["schemas"]["RetryOverride"] | null;
      reviewer?: components["schemas"]["AgentReviewer"] | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentOverride-Output"] | null;
      } | null;
      /** Toolsets */
      toolsets?: {
        [key: string]: components["schemas"]["ToolsetSelection"];
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
    /** ApplyDraftRequest */
    ApplyDraftRequest: {
      /** Content Digest */
      content_digest: string;
      /** Dependency Digest */
      dependency_digest: string;
      /** Expected Target Version */
      expected_target_version?: number | null;
      /** Expected Version */
      expected_version: number;
      verification_acknowledgement?:
        components["schemas"]["VerificationAcknowledgement"] | null;
      /**
       * Verification Run Ids
       * @default []
       */
      verification_run_ids?: string[];
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
    /** AssistantReadiness */
    AssistantReadiness: {
      /** Ready */
      ready: boolean;
      /**
       * Reason Code
       * @enum {string}
       */
      reason_code:
        | "ready"
        | "provider_setup_required"
        | "model_setup_required"
        | "model_access_denied"
        | "compatible_model_required";
      selected_model?: components["schemas"]["SelectedAssistantModel"] | null;
      /** Setup Actions */
      setup_actions: (
        "configure_provider" | "configure_model" | "contact_administrator"
      )[];
      /** Setup Url */
      setup_url: string;
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
    /** Authorization */
    Authorization: {
      /** Connection Id */
      connection_id: string;
      /** Error Code */
      error_code?: string | null;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Id */
      id: string;
      next_action?: components["schemas"]["AuthorizationAction"] | null;
      /**
       * Outcome Unknown
       * @default false
       */
      outcome_unknown?: boolean;
      /**
       * Status
       * @enum {string}
       */
      status:
        | "preparing"
        | "awaiting_user"
        | "awaiting_completion"
        | "processing"
        | "completed"
        | "failed"
        | "expired"
        | "cancelled";
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
    };
    /** AuthorizationAction */
    AuthorizationAction: {
      /** Client Registration */
      client_registration?: string | null;
      /**
       * Grant Types
       * @default []
       */
      grant_types?: string[];
      /** Issuer Url */
      issuer_url?: string | null;
      /** Redirect Uri */
      redirect_uri?: string | null;
      /**
       * Token Endpoint Auth Methods
       * @default []
       */
      token_endpoint_auth_methods?: string[];
      /**
       * Type
       * @enum {string}
       */
      type:
        "open_url" | "configure_oauth_client" | "check_connection" | "restart";
      /** Url */
      url?: string | null;
    };
    /** AuthorizationRedirect */
    AuthorizationRedirect: {
      /** Url */
      url: string;
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
    /** BotCheck */
    BotCheck: {
      /** Account Id */
      account_id: string;
      /**
       * Checked At
       * Format: date-time
       */
      checked_at: string;
      conversation?: components["schemas"]["ConversationInfo"] | null;
      /** Conversation Id */
      conversation_id?: string | null;
      /** Credential Generation */
      credential_generation: number;
      /** Error Code */
      error_code?: string | null;
      installation?: components["schemas"]["InstallationInfo"] | null;
    };
    /** BotCheckHistory */
    BotCheckHistory: {
      latest: components["schemas"]["BotCheck"] | null;
    };
    /** BotCheckRequest */
    BotCheckRequest: {
      /** Conversation Id */
      conversation_id?: string | null;
      /** Expected Version */
      expected_version: number;
    };
    /** BotCollection */
    BotCollection: {
      /** Items */
      items: components["schemas"]["BotSummary"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** BotReplyCollection */
    BotReplyCollection: {
      /** Items */
      items: components["schemas"]["BotReplyObservation"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** BotReplyObservation */
    BotReplyObservation: {
      /** Account Version */
      account_version: number;
      /** Credential Generation */
      credential_generation: number;
      /** Error Code */
      error_code: string | null;
      /** Finished At */
      finished_at: string | null;
      /** Id */
      id: string;
      /**
       * Provider Key
       * @enum {string}
       */
      provider_key: "slack" | "lark";
      /** Receipt */
      receipt:
        | components["schemas"]["SlackReplyReceipt"]
        | components["schemas"]["LarkReplyReceipt"]
        | null;
      /** Run Attempt Id */
      run_attempt_id: string;
      /** Run Id */
      run_id: string;
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
      /**
       * Status
       * @enum {string}
       */
      status: "dispatching" | "succeeded" | "rejected" | "outcome_unknown";
      /** Target Id */
      target_id: string | null;
      /** Test Id */
      test_id: string | null;
    };
    /** BotSetup */
    BotSetup: {
      /** Account Id */
      account_id: string;
      /** Event Path */
      event_path: string;
      /** Event Url */
      event_url: string | null;
    };
    /** BotSummary */
    BotSummary: {
      account: components["schemas"]["Account"];
      /** Checked At */
      checked_at: string | null;
      /** Configured Target Count */
      configured_target_count: number;
      /** External Organization Id */
      external_organization_id: string | null;
      /** External Organization Name */
      external_organization_name: string | null;
      memory_settings: components["schemas"]["AccountMemorySettings"];
      /**
       * Setup Condition
       * @enum {string}
       */
      setup_condition:
        | "disabled"
        | "needs_verification"
        | "check_failed"
        | "reception_off"
        | "receiving";
      /** Test Observed At */
      test_observed_at: string | null;
      /** Test Stage */
      test_stage:
        | (
            | "waiting"
            | "expired"
            | "stale"
            | "received"
            | "rejected"
            | "accepted"
            | "reply_confirmed"
          )
        | null;
    };
    /** BotTest */
    BotTest: {
      /** Accepted At */
      accepted_at: string | null;
      /** Account Id */
      account_id: string;
      /** Account Version */
      account_version: number;
      /** Admission Id */
      admission_id: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Credential Generation */
      credential_generation: number;
      /** Event Received At */
      event_received_at: string | null;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** External Target Id */
      external_target_id: string;
      /** Id */
      id: string;
      /** Rejection Code */
      rejection_code: string | null;
      reply?: components["schemas"]["BotReplyObservation"] | null;
      /** Run Id */
      run_id: string | null;
      /** Session Id */
      session_id?: string | null;
      /** Stale */
      stale: boolean;
      /** Steer Id */
      steer_id: string | null;
      /** Target Id */
      target_id: string;
      /** Target Version */
      target_version: number;
      /** Thread Id */
      thread_id?: string | null;
    };
    /** BotTestHistory */
    BotTestHistory: {
      latest: components["schemas"]["BotTest"] | null;
    };
    /** BotThread */
    BotThread: {
      /** Agent Id */
      agent_id: string;
      /** Binding Id */
      binding_id: string;
      /** Run Id */
      run_id: string;
      run_status: components["schemas"]["RunStatus"];
      /** Session Id */
      session_id: string;
      /** Thread Id */
      thread_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
    };
    /** BotThreadCollection */
    BotThreadCollection: {
      /** Items */
      items: components["schemas"]["BotThread"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** CatalogModel */
    CatalogModel: {
      declarations: components["schemas"]["ModelDeclarations-Output"];
      /** Identity */
      identity: string;
      /** Name */
      name: string;
      /** Pricing Warning */
      pricing_warning?: string | null;
      /** Provider Name */
      provider_name: string;
      ref: components["schemas"]["CatalogRef"];
      /**
       * Release Date
       * Format: date
       */
      release_date: string;
    };
    /**
     * CatalogRef
     * @description A models.dev provider-qualified identity, independent of the outbound ID.
     */
    CatalogRef: {
      /** Model */
      model: string;
      /** Provider */
      provider: string;
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
       * @default shared
       * @enum {string}
       */
      mode?: "none" | "shared" | "dedicated";
      /** Template Revision Id */
      template_revision_id?: string | null;
    };
    /** ClientConnectionStatus */
    ClientConnectionStatus: {
      /** Connection Id */
      connection_id: string | null;
      /** Error */
      error:
        | (
            | "environment_unavailable"
            | "environment_initialization_failed"
            | "control_draining"
          )
        | null;
      /**
       * Observed At
       * Format: date-time
       */
      observed_at: string;
      /**
       * Status
       * @enum {string}
       */
      status: "online" | "connecting" | "offline";
    };
    /** ClientConnectionTicket */
    ClientConnectionTicket: {
      /** Connection Id */
      connection_id: string;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Ticket */
      ticket: string;
      /** Websocket Url */
      websocket_url: string;
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
      /**
       * Permission
       * @default inherit
       * @enum {string}
       */
      permission?: "inherit" | "allow" | "deny";
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
    /** Collection_EnvironmentProviderDefinition_ */
    Collection_EnvironmentProviderDefinition_: {
      /** Items */
      items: components["schemas"]["EnvironmentProviderDefinition"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection_EnvironmentProvider_ */
    Collection_EnvironmentProvider_: {
      /** Items */
      items: components["schemas"]["EnvironmentProvider"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection_EnvironmentTemplateRevision_ */
    Collection_EnvironmentTemplateRevision_: {
      /** Items */
      items: components["schemas"]["EnvironmentTemplateRevision"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection_EnvironmentTemplate_ */
    Collection_EnvironmentTemplate_: {
      /** Items */
      items: components["schemas"]["EnvironmentTemplate"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** Collection_Environment_ */
    Collection_Environment_: {
      /** Items */
      items: components["schemas"]["Environment"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** CompleteAuthorizationRequest */
    CompleteAuthorizationRequest: {
      /** Code */
      code?: string | null;
      /** Completion Verifier */
      completion_verifier?: string | null;
      /** Error */
      error?: string | null;
      /** Iss */
      iss?: string | null;
      /** Receipt */
      receipt?: string | null;
      /** State */
      state?: string | null;
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
    /** ConfigurationApplicationCollection */
    ConfigurationApplicationCollection: {
      /** Items */
      items: components["schemas"]["ConfigurationApplicationReceipt"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ConfigurationApplicationReceipt */
    ConfigurationApplicationReceipt: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Agent Version */
      agent_version: number;
      /**
       * Applied At
       * Format: date-time
       */
      applied_at: string;
      /** Applied By User Id */
      applied_by_user_id: string;
      /** Draft Id */
      draft_id: string;
      /** No Change */
      no_change: boolean;
      /** Reviewed Base Agent Revision Id */
      reviewed_base_agent_revision_id: string | null;
      /** Reviewed Base Agent Version */
      reviewed_base_agent_version: number | null;
      reviewed_creation_metadata:
        components["schemas"]["CreationMetadata"] | null;
      /** Reviewed Digest */
      reviewed_digest: string;
      /**
       * Reviewed Mode
       * @enum {string}
       */
      reviewed_mode: "create" | "update";
      /** Reviewed Target Agent Id */
      reviewed_target_agent_id: string | null;
      /** Reviewed Version */
      reviewed_version: number;
      verification_acknowledgement?:
        components["schemas"]["VerificationAcknowledgement"] | null;
      /**
       * Verification Run Ids
       * @default []
       */
      verification_run_ids?: string[];
    };
    /** ConfigurationDifference */
    ConfigurationDifference: {
      after: components["schemas"]["JsonValue"];
      /** After Present */
      after_present: boolean;
      before: components["schemas"]["JsonValue"];
      /** Before Present */
      before_present: boolean;
      /** Path */
      path: string[];
    };
    /** ConfigurationDraft */
    ConfigurationDraft: {
      /** Base Agent Revision Id */
      base_agent_revision_id: string | null;
      /** Base Agent Version */
      base_agent_version?: number | null;
      config: components["schemas"]["AgentConfig-Output"] | null;
      /** Content Digest */
      content_digest: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      creation_metadata?: components["schemas"]["CreationMetadata"] | null;
      /**
       * Evidence Refs
       * @default []
       */
      evidence_refs?: string[];
      /** Id */
      id: string;
      latest_validation?:
        components["schemas"]["ConfigurationValidation"] | null;
      /**
       * Mode
       * @enum {string}
       */
      mode: "create" | "update";
      /** Organization Id */
      organization_id: string;
      /** Session Id */
      session_id: string;
      /** Source Agent Revision Id */
      source_agent_revision_id: string | null;
      /** Source Agent Revision Version */
      source_agent_revision_version?: number | null;
      /**
       * Source Selector
       * @enum {string}
       */
      source_selector: "current" | "explicit" | "empty";
      /**
       * Status
       * @enum {string}
       */
      status: "open" | "discarded" | "expired";
      /** Target Agent Id */
      target_agent_id: string | null;
      /** Terminal Reason */
      terminal_reason?: string | null;
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
    /** ConfigurationDraftReview */
    ConfigurationDraftReview: {
      base: components["schemas"]["ConfigurationRevisionView"] | null;
      /** Base Agent Revision Id */
      base_agent_revision_id: string | null;
      /** Base Agent Version */
      base_agent_version?: number | null;
      /** Base To Candidate */
      base_to_candidate: components["schemas"]["ConfigurationDifference"][];
      /** Base To Current Target */
      base_to_current_target: components["schemas"]["ConfigurationDifference"][];
      config: components["schemas"]["AgentConfig-Output"] | null;
      /** Content Digest */
      content_digest: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      creation_metadata?: components["schemas"]["CreationMetadata"] | null;
      current_target: components["schemas"]["ConfigurationRevisionView"] | null;
      /** Current Target To Candidate */
      current_target_to_candidate: components["schemas"]["ConfigurationDifference"][];
      /**
       * Evidence Refs
       * @default []
       */
      evidence_refs?: string[];
      /** Id */
      id: string;
      latest_application_receipt:
        components["schemas"]["ConfigurationApplicationReceipt"] | null;
      latest_validation?:
        components["schemas"]["ConfigurationValidation"] | null;
      /**
       * Mode
       * @enum {string}
       */
      mode: "create" | "update";
      /** Organization Id */
      organization_id: string;
      /** Session Id */
      session_id: string;
      source: components["schemas"]["ConfigurationRevisionView"] | null;
      /** Source Agent Revision Id */
      source_agent_revision_id: string | null;
      /** Source Agent Revision Version */
      source_agent_revision_version?: number | null;
      /**
       * Source Selector
       * @enum {string}
       */
      source_selector: "current" | "explicit" | "empty";
      /** Source To Candidate */
      source_to_candidate: components["schemas"]["ConfigurationDifference"][];
      /**
       * Status
       * @enum {string}
       */
      status: "open" | "discarded" | "expired";
      /** Target Agent Id */
      target_agent_id: string | null;
      /** Target Conflict */
      target_conflict: boolean;
      /** Terminal Reason */
      terminal_reason?: string | null;
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
    /** ConfigurationInputRequest */
    ConfigurationInputRequest: {
      /** Expected Thread Version */
      expected_thread_version: number;
      input: components["schemas"]["AgentInput"];
    };
    /** ConfigurationRevisionView */
    ConfigurationRevisionView: {
      /** Agent Id */
      agent_id: string;
      config: components["schemas"]["AgentConfig-Output"];
      /** Revision Id */
      revision_id: string;
      /** Version */
      version: number;
    };
    /** ConfigurationSessionCollection */
    ConfigurationSessionCollection: {
      /** Items */
      items: components["schemas"]["ConfigurationSessionView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ConfigurationSessionView */
    ConfigurationSessionView: {
      /** Configuration Draft Id */
      configuration_draft_id: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /**
       * Has Runs
       * @default false
       */
      has_runs?: boolean;
      /** Id */
      id: string;
      /** Organization Id */
      organization_id: string;
      /** Owner User Id */
      owner_user_id: string;
      /** Root Thread Id */
      root_thread_id: string;
      /** Title */
      title?: string | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** ConfigurationThreadCollection */
    ConfigurationThreadCollection: {
      /** Items */
      items: components["schemas"]["ConfigurationThreadView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ConfigurationThreadView */
    ConfigurationThreadView: {
      draft: components["schemas"]["ConfigurationDraft"];
      thread: components["schemas"]["Thread"];
    };
    /** ConfigurationValidation */
    ConfigurationValidation: {
      /**
       * Checked At
       * Format: date-time
       */
      checked_at: string;
      /** Content Digest */
      content_digest: string;
      /** Dependency Digest */
      dependency_digest: string;
      /** Draft Version */
      draft_version: number;
      /**
       * Warnings
       * @default []
       */
      warnings?: string[];
    };
    /** ConfigureMCPOAuthClientRequest */
    ConfigureMCPOAuthClientRequest: {
      client: components["schemas"]["MCPOAuthClientInput"] | null;
      /** Expected Version */
      expected_version: number;
    };
    /** ConfigureScope */
    ConfigureScope: {
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Expected Version */
      expected_version?: number | null;
      /** External Conversation Id */
      external_conversation_id: string;
      /**
       * Save On Request
       * @default true
       */
      save_on_request?: boolean;
      /**
       * Timezone
       * @default UTC
       */
      timezone?: string;
      /**
       * Use Memory
       * @default true
       */
      use_memory?: boolean;
      /**
       * Visibility
       * @default group
       * @enum {string}
       */
      visibility?: "group" | "installation";
    };
    /** Connection */
    Connection: {
      /** Authorization Generation */
      authorization_generation: number;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      /** Credential Configured */
      credential_configured: boolean;
      /** Id */
      id: string;
      last_check?: components["schemas"]["ConnectionCheck"] | null;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Safe Metadata */
      safe_metadata?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Source */
      source:
        | components["schemas"]["ConnectorSource"]
        | components["schemas"]["MCPSource"];
      status: components["schemas"]["ConnectionStatus"];
      status_reason?: components["schemas"]["ConnectionStatusReason"] | null;
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
    /** ConnectionCheck */
    ConnectionCheck: {
      /**
       * Checked At
       * Format: date-time
       */
      checked_at: string;
      /** Error Code */
      error_code?: string | null;
      /**
       * Scope
       * @enum {string}
       */
      scope: "provider_account" | "mcp_discovery";
      /**
       * Status
       * @enum {string}
       */
      status: "passed" | "action_required" | "unavailable";
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
    /** ConnectionCollection */
    ConnectionCollection: {
      /** Items */
      items: components["schemas"]["Connection"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** ConnectionCommandRequest */
    ConnectionCommandRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /**
     * ConnectionStatus
     * @enum {string}
     */
    ConnectionStatus: "pending" | "ready" | "action_required" | "disabled";
    /**
     * ConnectionStatusReason
     * @enum {string}
     */
    ConnectionStatusReason: "reauthorization_required" | "incompatible";
    /** ConnectionToolSelection */
    ConnectionToolSelection: {
      /** Connection Id */
      connection_id: string;
      /**
       * Defer Loading
       * @default false
       */
      defer_loading?: boolean;
      /** @default inherit */
      permission?: components["schemas"]["ToolPermissionSetting"];
      /** Permissions */
      permissions?: {
        [key: string]: components["schemas"]["ToolPermissionSetting"];
      };
      /** Tools */
      tools?: string[] | null;
    };
    /** Connector */
    Connector: {
      /** Authentication Methods */
      authentication_methods: string[];
      /** Connector Provider Id */
      connector_provider_id: string;
      /** Credential Schemas */
      credential_schemas?: {
        [key: string]: {
          [key: string]: components["schemas"]["JsonValue"];
        };
      };
      /** Description */
      description?: string | null;
      /** Key */
      key: string;
      /** Logo Url */
      logo_url?: string | null;
      /** Name */
      name: string;
      /** Setup Schema */
      setup_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Unavailable Reason */
      unavailable_reason?: string | null;
    };
    /** ConnectorCollection */
    ConnectorCollection: {
      /** Items */
      items: components["schemas"]["Connector"][];
      /** Next Cursor */
      next_cursor?: string | null;
      /** Refreshed At */
      refreshed_at?: string | null;
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
    /** ConnectorSource */
    ConnectorSource: {
      /** Connector Key */
      connector_key: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "connector";
      /** Provider Id */
      provider_id: string;
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
    /** Content */
    Content: {
      /** Media Type */
      media_type: string | null;
      value: components["schemas"]["JsonValue"];
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
    };
    /** ConversationCandidate */
    ConversationCandidate: {
      /** Id */
      id: string;
      /** Name */
      name: string;
    };
    /** ConversationInfo */
    ConversationInfo: {
      /**
       * Audience
       * @enum {string}
       */
      audience: "public" | "private" | "direct" | "unknown";
      /** External */
      external: boolean | null;
      /** Id */
      id: string;
      /** Is Active */
      is_active: boolean | null;
      /** Is Member */
      is_member: boolean | null;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id?: string | null;
    };
    /** ConversationPage */
    ConversationPage: {
      /** Cursor */
      cursor?: string | null;
      /** Items */
      items: components["schemas"]["ConversationCandidate"][];
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
      /** @default all_accessible */
      reception_scope?: components["schemas"]["ReceptionScope"];
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
    };
    /** CreateAgentRevisionRequest */
    CreateAgentRevisionRequest: {
      config: components["schemas"]["AgentConfig-Input"];
      /** Expected Version */
      expected_version: number;
    };
    /** CreateAuthorizationRequest */
    CreateAuthorizationRequest: {
      /** Completion Challenge */
      completion_challenge?: string | null;
      /** Credentials */
      credentials?: {
        [key: string]: string;
      } | null;
      /** Expected Version */
      expected_version: number;
      /**
       * Method
       * @enum {string}
       */
      method: "browser" | "credentials" | "client_credentials";
      /** Options */
      options?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Redirect Uri */
      redirect_uri?: string | null;
      /** Return Url */
      return_url?: string | null;
      /** State */
      state?: string | null;
    };
    /** CreateBotTest */
    CreateBotTest: {
      /** Expected Version */
      expected_version: number;
      /** Target Id */
      target_id: string;
      /** Target Version */
      target_version: number;
    };
    /** CreateConfigurationThreadRequest */
    CreateConfigurationThreadRequest: {
      /** Fork From Run Id */
      fork_from_run_id: string;
    };
    /** CreateConnectionRequest */
    CreateConnectionRequest: {
      /** Name */
      name: string;
      /** Source */
      source:
        | components["schemas"]["ConnectorSource"]
        | components["schemas"]["MCPSource"];
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
    /** CreateDocument */
    CreateDocument: {
      /** Activity Date */
      activity_date?: string | null;
      /** Correction Of */
      correction_of?: string | null;
      /**
       * Description
       * @default
       */
      description?: string;
      /**
       * Kind
       * @default long_term
       * @enum {string}
       */
      kind?: "daily" | "long_term";
      /** Text */
      text: string;
      /** Title */
      title: string;
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
    /** CreateManagedEnvironmentRequest */
    CreateManagedEnvironmentRequest: {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name?: string | null;
      /** Template Id */
      template_id: string;
      /** Version */
      version?: number | null;
    };
    /** CreateMemoryProviderRequest */
    CreateMemoryProviderRequest: {
      /** Configuration */
      configuration?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential */
      credential: {
        [key: string]: components["schemas"]["JsonValue"];
      };
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
      /** Extra Headers */
      extra_headers?: {
        [key: string]: string | null;
      };
      /** Name */
      name: string;
      /** Type */
      type: string;
    };
    /** CreateModelRequest */
    CreateModelRequest: {
      catalog_ref?: components["schemas"]["CatalogRef"] | null;
      declarations?: components["schemas"]["ModelDeclarations-Input"];
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
    /** CreateSessionRequest */
    CreateSessionRequest: {
      source?: components["schemas"]["SourceSelection"] | null;
      /** Target Agent Id */
      target_agent_id?: string | null;
    };
    /** CreateSkillRequest */
    CreateSkillRequest: {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Session Id */
      session_id?: string | null;
      /** Session Labels */
      session_labels?: {
        [key: string]: string;
      };
    };
    /** CreateWebProviderRequest */
    CreateWebProviderRequest: {
      /** Configuration */
      configuration?: {
        [key: string]: unknown;
      };
      /** Credential */
      credential: {
        [key: string]: unknown;
      };
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
    /** CreationMetadata */
    CreationMetadata: {
      /** Description */
      description?: string | null;
      /** Name */
      name: string;
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
    /** DiscardDraftRequest */
    DiscardDraftRequest: {
      /** Expected Version */
      expected_version: number;
    };
    /** DiscoverFeishuInstallationRequest */
    DiscoverFeishuInstallationRequest: {
      /** App Id */
      app_id: string;
      /**
       * App Secret
       * Format: password
       */
      app_secret: string;
    };
    /** Document */
    Document: {
      /**
       * Access Reasons
       * @default []
       */
      access_reasons?: components["schemas"]["DocumentAccessReason"][];
      /**
       * Activity Date
       * Format: date
       */
      activity_date: string;
      /** Correction Of */
      correction_of?: string | null;
      /** Description */
      description: string;
      /** Id */
      id: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: "daily" | "long_term";
      /** Owner Name */
      owner_name?: string | null;
      /** Path */
      path: string;
      /** Publication Source Id */
      publication_source_id?: string | null;
      /** Saved At */
      saved_at: string | null;
      /** Scope Id */
      scope_id: string;
      /**
       * Shared
       * @default false
       */
      shared?: boolean;
      /**
       * State
       * @enum {string}
       */
      state: "pending" | "active" | "unconfirmed" | "deleting" | "deleted";
      /** Text */
      text: string;
      /** Timezone */
      timezone: string;
      /** Title */
      title: string;
      /**
       * Version
       * @default 1
       */
      version?: number;
    };
    /** DocumentAccessReason */
    DocumentAccessReason: {
      /**
       * Kind
       * @enum {string}
       */
      kind: "owner" | "installation";
    };
    /** DocumentCollection */
    DocumentCollection: {
      /** Items */
      items: components["schemas"]["DocumentEntry"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** DocumentEntry */
    DocumentEntry: {
      /**
       * Activity Date
       * Format: date
       */
      activity_date: string;
      /** Correction Of */
      correction_of?: string | null;
      /** Description */
      description: string;
      /** Id */
      id: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: "daily" | "long_term";
      /** Path */
      path: string;
      /** Publication Source Id */
      publication_source_id?: string | null;
      /** Saved At */
      saved_at: string | null;
      /** Scope Id */
      scope_id: string;
      /**
       * Shared
       * @default false
       */
      shared?: boolean;
      /**
       * State
       * @enum {string}
       */
      state: "pending" | "active" | "unconfirmed" | "deleting" | "deleted";
      /** Timezone */
      timezone: string;
      /** Title */
      title: string;
      /**
       * Version
       * @default 1
       */
      version?: number;
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
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
       * Configuration Source
       * @default user
       * @enum {string}
       */
      configuration_source?: "user" | "deployment";
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
    /** EnvironmentProviderDefinition */
    EnvironmentProviderDefinition: {
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Configuration Versions */
      configuration_versions: string[];
      /** Credential Schema */
      credential_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Deployment Managed
       * @default false
       */
      deployment_managed?: boolean;
      /** Display Name */
      display_name: string;
      /** Requires Keepalive */
      requires_keepalive: boolean;
      /** Supports Destroy */
      supports_destroy: boolean;
      /** Supports Managed */
      supports_managed: boolean;
      /** Supports Stop */
      supports_stop: boolean;
      /** Template Configuration Schemas */
      template_configuration_schemas: {
        [key: string]: {
          [key: string]: components["schemas"]["JsonValue"];
        };
      };
      /** Type */
      type: string;
    };
    /** EnvironmentSelection */
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
    /** ErrorDetail */
    ErrorDetail: {
      /** Code */
      code: string;
      /** Details */
      details: {
        [key: string]: unknown;
      };
      /** Message */
      message: string;
      /** Request Id */
      request_id: string;
    };
    /** ErrorResponse */
    ErrorResponse: {
      error: components["schemas"]["ErrorDetail"];
    };
    /** EventConnectionStatus */
    EventConnectionStatus: {
      /** Error Code */
      error_code?: string | null;
      /** Last Event At */
      last_event_at?: string | null;
      /** Observed At */
      observed_at?: string | null;
      /**
       * State
       * @enum {string}
       */
      state:
        | "http"
        | "disabled"
        | "connecting"
        | "connected"
        | "reconnecting"
        | "disconnected";
      /**
       * Transport
       * @enum {string}
       */
      transport: "http" | "websocket";
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Thread Labels */
      thread_labels?: {
        [key: string]: string;
      };
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
      /** Connection Tools */
      connection_tools?:
        components["schemas"]["ConnectionToolSelection"][] | null;
      model?: components["schemas"]["ModelOverride"] | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
    };
    /** InstallationInfo */
    InstallationInfo: {
      /** App Id */
      app_id: string;
      /** Bot Id */
      bot_id: string;
      /** Bot Name */
      bot_name: string;
      /** Enabled */
      enabled: boolean;
      /** Enterprise Id */
      enterprise_id?: string | null;
      /** Organization Id */
      organization_id: string;
      /** Organization Name */
      organization_name: string;
    };
    /** InstrumentationScope */
    InstrumentationScope: {
      /** Attributes */
      attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Name */
      name: string | null;
      /** Version */
      version: string | null;
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
    /** JsonValue */
    JsonValue: unknown;
    /**
     * LabelsBody
     * @description Complete replacement body for a resource label map.
     */
    LabelsBody: {
      /** Labels */
      labels: {
        [key: string]: string;
      };
    };
    /** LarkReplyReceipt */
    LarkReplyReceipt: {
      /** Message Id */
      message_id: string;
      /** Request Id */
      request_id: string;
      /** Root Id */
      root_id?: string | null;
      /** Thread Id */
      thread_id?: string | null;
    };
    /** LaunchAuthorizationRequest */
    LaunchAuthorizationRequest: {
      /** Browser Nonce */
      browser_nonce: string;
      /** Token */
      token: string;
    };
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
    /** MCPOAuthClientConfiguration */
    MCPOAuthClientConfiguration: {
      /** Client Id */
      client_id: string;
      /**
       * Grant Type
       * @enum {string}
       */
      grant_type: "authorization_code" | "client_credentials";
      /** Issuer Url */
      issuer_url: string;
      /** Redirect Uri */
      redirect_uri?: string | null;
      /**
       * Source
       * @enum {string}
       */
      source: "pre_registered" | "dynamic";
      /**
       * Token Endpoint Auth Method
       * @enum {string}
       */
      token_endpoint_auth_method:
        "none" | "client_secret_basic" | "client_secret_post";
    };
    /** MCPOAuthClientInput */
    MCPOAuthClientInput: {
      /** Client Id */
      client_id: string;
      /** Client Secret */
      client_secret?: string | null;
      /**
       * Grant Type
       * @default authorization_code
       * @enum {string}
       */
      grant_type?: "authorization_code" | "client_credentials";
      /** Issuer Url */
      issuer_url: string;
      /** Redirect Uri */
      redirect_uri?: string | null;
      /**
       * Token Endpoint Auth Method
       * @enum {string}
       */
      token_endpoint_auth_method:
        "none" | "client_secret_basic" | "client_secret_post";
    };
    /** MCPOAuthDiscovery */
    MCPOAuthDiscovery: {
      /** Authorization Response Iss Parameter Supported */
      authorization_response_iss_parameter_supported: boolean;
      /**
       * Client Registration
       * @enum {string}
       */
      client_registration: "dynamic" | "manual";
      /** Grant Types Supported */
      grant_types_supported: ("authorization_code" | "client_credentials")[];
      /** Issuer Url */
      issuer_url: string;
      /** Redirect Uri */
      redirect_uri: string | null;
      /** Token Endpoint Auth Methods Supported */
      token_endpoint_auth_methods_supported: (
        "none" | "client_secret_basic" | "client_secret_post"
      )[];
    };
    /** MCPOAuthSetup */
    MCPOAuthSetup: {
      client?: components["schemas"]["MCPOAuthClientConfiguration"] | null;
      next_action: components["schemas"]["MCPOAuthSetupAction"];
    };
    /** MCPOAuthSetupAction */
    MCPOAuthSetupAction: {
      /** Client Registration */
      client_registration?: ("dynamic" | "manual") | null;
      /** Documentation Url */
      documentation_url?: string | null;
      /**
       * Grant Types
       * @default []
       */
      grant_types?: ("authorization_code" | "client_credentials")[];
      /** Issuer Url */
      issuer_url?: string | null;
      /** Redirect Uri */
      redirect_uri?: string | null;
      /**
       * Token Endpoint Auth Methods
       * @default []
       */
      token_endpoint_auth_methods?: (
        "none" | "client_secret_basic" | "client_secret_post"
      )[];
      /**
       * Type
       * @enum {string}
       */
      type:
        | "configure_oauth_client"
        | "start_authorization"
        | "authenticate_client_credentials"
        | "check_connection"
        | "completed";
    };
    /** MCPOAuthSetupRequest */
    MCPOAuthSetupRequest: {
      /** Redirect Uri */
      redirect_uri?: string | null;
    };
    /** MCPServer */
    MCPServer: {
      /**
       * Auth Mode
       * @enum {string}
       */
      auth_mode: "none" | "bearer" | "oauth" | "static_headers";
      /** Description */
      description: string;
      /** Documentation Url */
      documentation_url?: string | null;
      /** Endpoint Url */
      endpoint_url: string;
      /** Key */
      key: string;
      /** Logo Url */
      logo_url?: string | null;
      /** Name */
      name: string;
      /**
       * Origin
       * @default builtin
       * @enum {string}
       */
      origin?: "builtin" | "deployment";
      /**
       * Requirements
       * @default
       */
      requirements?: string;
      /**
       * Static Header Names
       * @default []
       */
      static_header_names?: string[];
    };
    /** MCPServerCollection */
    MCPServerCollection: {
      /** Items */
      items: components["schemas"]["MCPServer"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** MCPSource */
    MCPSource: {
      /**
       * Auth Mode
       * @enum {string}
       */
      auth_mode: "none" | "bearer" | "oauth" | "static_headers";
      /** Endpoint Url */
      endpoint_url: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "mcp";
      /**
       * Static Header Names
       * @default []
       */
      static_header_names?: string[];
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
    /** Memory */
    Memory: {
      /** Id */
      id: string;
      /** Memory */
      memory: string;
      /** Score */
      score?: number | null;
    };
    /**
     * MemoryAccess
     * @description Current subject permissions; each content operation authorizes again.
     */
    MemoryAccess: {
      /** Can Write */
      can_write: boolean;
    };
    /** MemoryCollection */
    MemoryCollection: {
      /** Items */
      items: components["schemas"]["Memory"][];
      /** @description Native pagination when available. Null means a bounded result, not a complete collection. */
      pagination?: components["schemas"]["MemoryPagination"] | null;
    };
    /** MemoryIndex */
    MemoryIndex: {
      /** Entries */
      entries: components["schemas"]["DocumentEntry"][];
      /** Next Cursor */
      next_cursor?: string | null;
      /**
       * Path
       * @default MEMORY.md
       * @constant
       */
      path?: "MEMORY.md";
      /** Text */
      text: string;
    };
    /**
     * MemoryPagination
     * @description Native traversal; a null cursor means the final page of that traversal.
     */
    MemoryPagination: {
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** MemoryProvider */
    MemoryProvider: {
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
    /** MemoryProviderCollection */
    MemoryProviderCollection: {
      /** Items */
      items: components["schemas"]["MemoryProvider"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** MemoryProviderDefinition */
    MemoryProviderDefinition: {
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: unknown;
      };
      /** Credential Schema */
      credential_schema: {
        [key: string]: unknown;
      };
      /** Display Name */
      display_name: string;
      /**
       * Supports Documents
       * @default false
       */
      supports_documents?: boolean;
      /** Type */
      type: string;
    };
    /** MemoryProviderDefinitionCollection */
    MemoryProviderDefinitionCollection: {
      /** Items */
      items: components["schemas"]["MemoryProviderDefinition"][];
    };
    /** MemoryProviderReference */
    MemoryProviderReference: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Is Current */
      is_current: boolean;
      /** Version */
      version: number;
    };
    /** MemoryProviderReferenceCollection */
    MemoryProviderReferenceCollection: {
      /** Items */
      items: components["schemas"]["MemoryProviderReference"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /**
     * MemoryScope
     * @enum {string}
     */
    MemoryScope: "thread" | "agent" | "user";
    /** MemorySearch */
    MemorySearch: {
      /**
       * Limit
       * @default 20
       */
      limit?: number;
      /** Query */
      query: string;
      /** Threshold */
      threshold?: number | null;
    };
    /**
     * MemorySelection
     * @description Opt-in Agent behavior; backend credentials and subject IDs are host-owned.
     */
    MemorySelection: {
      /**
       * Auto Recall
       * @default true
       */
      auto_recall?: boolean;
      /** Provider Id */
      provider_id: string;
      /**
       * Recall Limit
       * @default 5
       */
      recall_limit?: number;
      /**
       * Recall Required
       * @default false
       */
      recall_required?: boolean;
      /** Recall Threshold */
      recall_threshold?: number | null;
      /**
       * Recall Timeout
       * @default 2
       */
      recall_timeout?: number;
      scope?: components["schemas"]["MemoryScope"] | null;
      /**
       * Toolset
       * @default true
       */
      toolset?: boolean;
    };
    /** MemorySettings */
    MemorySettings: {
      /** Provider Id */
      provider_id: string;
      /**
       * Save On Request
       * @default true
       */
      save_on_request?: boolean;
      /**
       * Timezone
       * @default UTC
       */
      timezone?: string;
      /**
       * Use Memory
       * @default true
       */
      use_memory?: boolean;
    };
    /** MemoryWrite */
    MemoryWrite: {
      /** Text */
      text: string;
    };
    /** MessagingPolicy */
    MessagingPolicy: {
      /**
       * Interaction Mode
       * @enum {string}
       */
      interaction_mode: "mention" | "discussion" | "chat";
      /**
       * Reply Mode
       * @enum {string}
       */
      reply_mode: "auto" | "thread" | "main";
    };
    /** Model */
    Model: {
      catalog_ref?: components["schemas"]["CatalogRef"] | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      created_by: components["schemas"]["PrincipalRef"];
      declarations?: components["schemas"]["ModelDeclarations-Output"];
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
    /**
     * ModelCapability
     * @description Harness-owned capabilities of the active Agent model.
     * @enum {string}
     */
    ModelCapability:
      "image_understanding" | "video_understanding" | "audio_understanding";
    /** ModelCatalogCollection */
    ModelCatalogCollection: {
      /**
       * Items
       * @default []
       */
      items?: components["schemas"]["CatalogModel"][];
      /**
       * Released Since
       * Format: date
       */
      released_since: string;
      /**
       * Status
       * @enum {string}
       */
      status: "ready" | "stale" | "unavailable";
    };
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
    /**
     * ModelDeclarations-Input
     * @description Harness-facing facts and authoring choices declared for one saved Model.
     */
    "ModelDeclarations-Input": {
      /** Capabilities */
      capabilities?: components["schemas"]["ModelCapability"][];
      /** Context Window Tokens */
      context_window_tokens?: number | null;
      pricing?: components["schemas"]["TokenPricing-Input"] | null;
      /** Structured Output */
      structured_output?: boolean | null;
      /** Supports Tools */
      supports_tools?: boolean | null;
    };
    /**
     * ModelDeclarations-Output
     * @description Harness-facing facts and authoring choices declared for one saved Model.
     */
    "ModelDeclarations-Output": {
      /** Capabilities */
      capabilities?: components["schemas"]["ModelCapability"][];
      /** Context Window Tokens */
      context_window_tokens?: number | null;
      pricing?: components["schemas"]["TokenPricing-Output"] | null;
      /** Structured Output */
      structured_output?: boolean | null;
      /** Supports Tools */
      supports_tools?: boolean | null;
    };
    /** ModelIdentity */
    ModelIdentity: {
      /** Requested */
      requested: string | null;
      /** Response */
      response: string | null;
    };
    /** ModelOverride */
    ModelOverride: {
      characteristics?:
        components["schemas"]["AgentModelCharacteristics"] | null;
      /** Model Key */
      model_key?: string | null;
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
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
      /**
       * Header Names
       * @default []
       */
      header_names?: string[];
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
      /**
       * Catalog Providers
       * @default []
       */
      catalog_providers?: string[];
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
      /** Model Api Labels */
      model_api_labels: {
        [key: string]: string;
      };
      /** Settings Schemas */
      settings_schemas: {
        [key: string]: {
          [key: string]: unknown;
        };
      };
      /** Supported Model Apis */
      supported_model_apis: string[];
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      /** Attributes */
      attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Cost Usd */
      cost_usd: string | null;
      /** Ended At */
      ended_at: string | null;
      /** Events */
      events: components["schemas"]["ObservationEvent"][] | null;
      /** Id */
      id: string;
      input: components["schemas"]["Content"] | null;
      /** Level */
      level: string | null;
      /** Links */
      links: components["schemas"]["ObservationLink"][] | null;
      model: components["schemas"]["ModelIdentity"] | null;
      /** Name */
      name: string;
      output: components["schemas"]["Content"] | null;
      /** Parent Id */
      parent_id: string | null;
      /** Resource Attributes */
      resource_attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      scope: components["schemas"]["InstrumentationScope"] | null;
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
      /** Status */
      status: ("unset" | "ok" | "error") | null;
      /** Status Message */
      status_message: string | null;
      /** Type */
      type: string;
      /** Usage */
      usage: {
        [key: string]: number;
      } | null;
    };
    /** ObservationCollection */
    ObservationCollection: {
      /** Items */
      items: components["schemas"]["Observation"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ObservationEvent */
    ObservationEvent: {
      /** Attributes */
      attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Name */
      name: string;
      /**
       * Occurred At
       * Format: date-time
       */
      occurred_at: string;
    };
    /** ObservationLink */
    ObservationLink: {
      /** Attributes */
      attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Observation Id */
      observation_id: string;
      /** Trace Id */
      trace_id: string;
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
    /** Page_ApiKey_ */
    Page_ApiKey_: {
      /** Items */
      items: components["schemas"]["ApiKey"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_AuthSession_ */
    Page_AuthSession_: {
      /** Items */
      items: components["schemas"]["AuthSession"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_Invitation_ */
    Page_Invitation_: {
      /** Items */
      items: components["schemas"]["Invitation"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_Organization_ */
    Page_Organization_: {
      /** Items */
      items: components["schemas"]["Organization"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_RoleBinding_ */
    Page_RoleBinding_: {
      /** Items */
      items: components["schemas"]["RoleBinding"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_SecurityEvent_ */
    Page_SecurityEvent_: {
      /** Items */
      items: components["schemas"]["SecurityEvent"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_ServiceAccount_ */
    Page_ServiceAccount_: {
      /** Items */
      items: components["schemas"]["ServiceAccount"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_User_ */
    Page_User_: {
      /** Items */
      items: components["schemas"]["User"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Page_Workspace_ */
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
    /** RebaseDraftRequest */
    RebaseDraftRequest: {
      config: components["schemas"]["AgentConfig-Input"];
      /** Expected Target Version */
      expected_target_version: number;
      /** Expected Version */
      expected_version: number;
    };
    /** ReceiveAuthorizationRequest */
    ReceiveAuthorizationRequest: {
      /** Browser Nonce */
      browser_nonce: string;
      /** Session Uri */
      session_uri?: string | null;
    };
    /**
     * ReceptionScope
     * @enum {string}
     */
    ReceptionScope: "all_accessible" | "configured_targets";
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name?: string | null;
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
      /** Reason */
      reason?: string | null;
    };
    /** RemoveOperation */
    RemoveOperation: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      op: "remove";
      /** Path */
      path: string[];
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
    /** ReplaceMemorySettings */
    ReplaceMemorySettings: {
      /** Expected Version */
      expected_version: number;
      memory: components["schemas"]["MemorySettings"] | null;
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
    /** ReplaceTextOperation */
    ReplaceTextOperation: {
      /** New Text */
      new_text: string;
      /** Old Text */
      old_text: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      op: "replace_text";
      /** Path */
      path: string[];
    };
    /** ResolvedAgentModel */
    ResolvedAgentModel: {
      characteristics: components["schemas"]["AgentModelCharacteristics"];
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      agent_revision_id: string | null;
      /** Completed At */
      completed_at: string | null;
      /** Configuration Draft Id */
      configuration_draft_id?: string | null;
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
      /** Labels */
      labels: {
        [key: string]: string;
      };
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
    /** Scope */
    Scope: {
      /** Account Id */
      account_id: string;
      /**
       * Audience
       * @enum {string}
       */
      audience: "public" | "private" | "direct" | "unknown";
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** External Conversation Id */
      external_conversation_id: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Provider Id */
      provider_id: string;
      /**
       * Save On Request
       * @default true
       */
      save_on_request?: boolean;
      /**
       * Timezone
       * @default UTC
       */
      timezone?: string;
      /**
       * Use Memory
       * @default true
       */
      use_memory?: boolean;
      /** Version */
      version: number;
      /**
       * Visibility
       * @default group
       * @enum {string}
       */
      visibility?: "group" | "installation";
    };
    /** ScopeCollection */
    ScopeCollection: {
      /** Items */
      items: components["schemas"]["Scope"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** SearchDocuments */
    SearchDocuments: {
      /**
       * Include Shared
       * @default true
       */
      include_shared?: boolean;
      /**
       * Limit
       * @default 20
       */
      limit?: number;
      /** Query */
      query: string;
    };
    /**
     * SearchIn
     * @enum {string}
     */
    SearchIn: "input" | "output" | "input_output";
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
    /** SelectedAssistantModel */
    SelectedAssistantModel: {
      /** Model Id */
      model_id: string;
      /** Model Key */
      model_key: string;
      /** Selection Reason */
      selection_reason: string;
      /** Settings */
      settings: {
        [key: string]: components["schemas"]["JsonValue"];
      };
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
      /** Agent Name */
      agent_name: string | null;
      /** Input Text */
      input_text: string | null;
      /** Output Text */
      output_text: string | null;
      /** Run Id */
      run_id: string;
      run_status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
      /** Trigger Type */
      trigger_type: string;
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
      /** Labels */
      labels: {
        [key: string]: string;
      };
      preview: components["schemas"]["SessionPreview"] | null;
      /** Run Count */
      run_count: number | null;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** SetOperation */
    SetOperation: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      op: "set";
      /** Path */
      path: string[];
      value: components["schemas"]["JsonValue"];
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      items: components["schemas"]["SkillListItem"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SkillListItem */
    SkillListItem: {
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /**
       * Source Kind
       * @enum {string}
       */
      source_kind: "zip" | "github";
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
    /** SlackReplyReceipt */
    SlackReplyReceipt: {
      /** Channel Id */
      channel_id: string;
      /** Message Ts */
      message_ts: string;
      /** Request Id */
      request_id: string;
      /** Root Thread Ts */
      root_thread_ts: string;
    };
    /** SourceSelection */
    SourceSelection: {
      /** Revision Id */
      revision_id?: string | null;
      /**
       * Selector
       * @default current
       * @enum {string}
       */
      selector?: "current" | "explicit" | "empty";
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Session Id */
      session_id?: string | null;
      /** Session Labels */
      session_labels?: {
        [key: string]: string;
      };
      /** Thread Labels */
      thread_labels?: {
        [key: string]: string;
      };
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
    /** SubagentOverride-Input */
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
    /** SubagentOverride-Output */
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
    /** SubagentSelection-Input */
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
    /** SubagentSelection-Output */
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      /** Configuration Draft Id */
      configuration_draft_id?: string | null;
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
      /** Labels */
      labels: {
        [key: string]: string;
      };
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
    /** ThreadRunSubmissionIntent-Input */
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
    };
    /** ThreadRunSubmissionIntent-Output */
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      waiting_resolution?:
        components["schemas"]["WaitingResolutionDefaults"] | null;
    };
    /**
     * TokenPriceTier-Input
     * @description Complete rates for requests strictly above the input threshold.
     */
    "TokenPriceTier-Input": {
      /** Above */
      above?: number | null;
      rates: components["schemas"]["TokenRates-Input"];
    };
    /**
     * TokenPriceTier-Output
     * @description Complete rates for requests strictly above the input threshold.
     */
    "TokenPriceTier-Output": {
      /** Above */
      above?: number | null;
      rates: components["schemas"]["TokenRates-Output"];
    };
    /**
     * TokenPricing-Input
     * @description Cliff prices: one input-length tier prices the entire request.
     */
    "TokenPricing-Input": {
      /**
       * Currency
       * @default USD
       * @constant
       */
      currency?: "USD";
      /**
       * Tier Basis
       * @default input_tokens
       * @constant
       */
      tier_basis?: "input_tokens";
      /** Tiers */
      tiers: components["schemas"]["TokenPriceTier-Input"][];
      /**
       * Unit
       * @default million_tokens
       * @constant
       */
      unit?: "million_tokens";
    };
    /**
     * TokenPricing-Output
     * @description Cliff prices: one input-length tier prices the entire request.
     */
    "TokenPricing-Output": {
      /**
       * Currency
       * @default USD
       * @constant
       */
      currency?: "USD";
      /**
       * Tier Basis
       * @default input_tokens
       * @constant
       */
      tier_basis?: "input_tokens";
      /** Tiers */
      tiers: components["schemas"]["TokenPriceTier-Output"][];
      /**
       * Unit
       * @default million_tokens
       * @constant
       */
      unit?: "million_tokens";
    };
    /**
     * TokenRates-Input
     * @description USD per million tokens; null is unknown, never free.
     */
    "TokenRates-Input": {
      /** Cache Read */
      cache_read?: number | string | null;
      /** Cache Write */
      cache_write?: number | string | null;
      /** Input */
      input?: number | string | null;
      /** Output */
      output?: number | string | null;
    };
    /**
     * TokenRates-Output
     * @description USD per million tokens; null is unknown, never free.
     */
    "TokenRates-Output": {
      /** Cache Read */
      cache_read?: string | null;
      /** Cache Write */
      cache_write?: string | null;
      /** Input */
      input?: string | null;
      /** Output */
      output?: string | null;
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
    /** ToolDefinition */
    ToolDefinition: {
      /** Config Schema */
      config_schema: {
        [key: string]: unknown;
      };
      /** Default Enabled */
      default_enabled: boolean;
      default_permission: components["schemas"]["ToolPermissionMode"];
      /**
       * Deployment Supported
       * @default true
       */
      deployment_supported?: boolean;
      /** Display Name */
      display_name: string;
      /** Execution Id */
      execution_id: string;
      /** Key */
      key: string;
      /** Model Name */
      model_name: string;
      resource_selector?: components["schemas"]["ToolResourceSelector"] | null;
      /** Supported Permissions */
      supported_permissions: (
        "inherit" | "allow" | "ask" | "deny" | "review"
      )[];
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
    /**
     * ToolPermissionMode
     * @enum {string}
     */
    ToolPermissionMode: "allow" | "deny" | "ask" | "review";
    /** ToolPermissionSetting */
    ToolPermissionSetting:
      components["schemas"]["ToolPermissionMode"] | "inherit";
    /** ToolResourceSelector */
    ToolResourceSelector: {
      /**
       * Kind
       * @constant
       */
      kind: "web_provider";
      /**
       * Operation
       * @enum {string}
       */
      operation: "search" | "scrape";
    };
    /**
     * ToolReviewRule
     * @description A matching rule overrides the supplied fields of the global policy.
     */
    ToolReviewRule: {
      /** On Flagged */
      on_flagged?: ("deny" | "approval_required") | null;
      risk_threshold?: components["schemas"]["ToolRiskLevel"] | null;
    };
    /**
     * ToolRiskLevel
     * @enum {string}
     */
    ToolRiskLevel: "low" | "medium" | "high" | "extra_high";
    /** ToolSelection */
    ToolSelection: {
      /** Config */
      config?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** @default inherit */
      permission?: components["schemas"]["ToolPermissionSetting"];
    };
    /** ToolSetupDestination */
    ToolSetupDestination: {
      /**
       * Kind
       * @enum {string}
       */
      kind: "web_provider" | "reviewer";
      /** Operation */
      operation?: ("search" | "scrape") | null;
    };
    /** ToolsetCandidate */
    ToolsetCandidate: {
      reviewer?: components["schemas"]["AgentReviewer"] | null;
      /** Toolsets */
      toolsets?: {
        [key: string]: components["schemas"]["ToolsetSelection"];
      };
    };
    /** ToolsetCandidateError */
    ToolsetCandidateError: {
      /** Code */
      code: string;
      /** Path */
      path: string;
      setup_destination?: components["schemas"]["ToolSetupDestination"] | null;
    };
    /** ToolsetCandidateResult */
    ToolsetCandidateResult: {
      /** Errors */
      errors: components["schemas"]["ToolsetCandidateError"][];
      /** Toolsets */
      toolsets: {
        [key: string]: components["schemas"]["ToolsetSelection"];
      };
      /** Valid */
      valid: boolean;
    };
    /** ToolsetCatalog */
    ToolsetCatalog: {
      /** Items */
      items: components["schemas"]["ToolsetDefinition"][];
    };
    /** ToolsetDefinition */
    ToolsetDefinition: {
      /** Config Schema */
      config_schema: {
        [key: string]: unknown;
      };
      /** Default Enabled */
      default_enabled: boolean;
      /** Display Name */
      display_name: string;
      /**
       * Key
       * @enum {string}
       */
      key: "files" | "shell" | "web" | "assets";
      /** Tools */
      tools: components["schemas"]["ToolDefinition"][];
    };
    /** ToolsetSelection */
    ToolsetSelection: {
      /** Config */
      config?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Tools */
      tools?: {
        [key: string]: components["schemas"]["ToolSelection"];
      };
    };
    /** Trace */
    Trace: {
      correlation: components["schemas"]["TraceCorrelation"];
      /** Id */
      id: string;
      /** Provider */
      provider: string;
      root: components["schemas"]["Observation"];
      /** Source Url */
      source_url: string | null;
    };
    /** TraceCollection */
    TraceCollection: {
      /** Items */
      items: components["schemas"]["Trace"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** TraceCorrelation */
    TraceCorrelation: {
      /** Agent Id */
      agent_id: string;
      /** Organization Id */
      organization_id: string;
      /** Run Attempt Id */
      run_attempt_id: string;
      /** Run Id */
      run_id: string;
      /** Session Id */
      session_id: string;
      /** Thread Id */
      thread_id: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** TraceQueryDescriptor */
    TraceQueryDescriptor: {
      /** Enabled */
      enabled: boolean;
      /** History From */
      history_from: string | null;
      /** Provider */
      provider: string;
      /** Search In */
      search_in: components["schemas"]["SearchIn"][];
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
      reception_scope?: components["schemas"]["ReceptionScope"] | null;
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
    /** UpdateConfigurationDraftRequest */
    UpdateConfigurationDraftRequest: {
      creation_metadata?: components["schemas"]["CreationMetadata"] | null;
      /** Expected Digest */
      expected_digest?: string | null;
      /** Expected Version */
      expected_version: number;
      /**
       * Operations
       * @default []
       */
      operations?: (
        | components["schemas"]["SetOperation"]
        | components["schemas"]["RemoveOperation"]
        | components["schemas"]["ReplaceTextOperation"]
      )[];
    };
    /** UpdateConnectionRequest */
    UpdateConnectionRequest: {
      /** Expected Version */
      expected_version: number;
      /** Name */
      name: string;
    };
    /** UpdateConnectorProviderRequest */
    UpdateConnectorProviderRequest: {
      /** Credentials */
      credentials?: {
        [key: string]: string;
      } | null;
      /** Expected Version */
      expected_version: number;
      /** Name */
      name?: string | null;
      status?: components["schemas"]["ConnectorProviderStatus"] | null;
    };
    /** UpdateEnvironmentRequest */
    UpdateEnvironmentRequest: {
      /** Name */
      name: string;
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
    /** UpdateMemoryProviderRequest */
    UpdateMemoryProviderRequest: {
      /** Credential */
      credential?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Name */
      name?: string | null;
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
      /** Extra Headers */
      extra_headers?: {
        [key: string]: string | null;
      };
      /** Name */
      name?: string | null;
    };
    /** UpdateModelRequest */
    UpdateModelRequest: {
      catalog_ref?: components["schemas"]["CatalogRef"] | null;
      declarations?: components["schemas"]["ModelDeclarations-Input"] | null;
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
      /** Credential */
      credential?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
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
    /** UpdateWebProviderRequest */
    UpdateWebProviderRequest: {
      /** Configuration */
      configuration?: {
        [key: string]: unknown;
      } | null;
      /** Credential */
      credential?: {
        [key: string]: unknown;
      } | null;
      /** Enabled */
      enabled?: boolean | null;
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
     * UsageLimits-Input
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
     * UsageLimits-Output
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
    /** VerificationAcknowledgement */
    VerificationAcknowledgement: {
      /**
       * Outcome
       * @enum {string}
       */
      outcome: "unverified" | "failed";
      /** Reason */
      reason: string;
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
      /** Labels */
      labels?: {
        [key: string]: string;
      };
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
    /** WebProvider */
    WebProvider: {
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
    /** WebProviderCollection */
    WebProviderCollection: {
      /** Items */
      items: components["schemas"]["WebProvider"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** WebProviderDefinition */
    WebProviderDefinition: {
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
      /** Operations */
      operations: ("search" | "scrape")[];
      /** Setup Url */
      setup_url: string;
      /**
       * Supports Restricted Scrape
       * @default false
       */
      supports_restricted_scrape?: boolean;
      /** Type */
      type: string;
    };
    /** WebProviderDefinitionCollection */
    WebProviderDefinitionCollection: {
      /** Items */
      items: components["schemas"]["WebProviderDefinition"][];
    };
    /** WebProviderReference */
    WebProviderReference: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Is Current */
      is_current: boolean;
      /** Version */
      version: number;
    };
    /** WebProviderReferenceCollection */
    WebProviderReferenceCollection: {
      /** Items */
      items: components["schemas"]["WebProviderReference"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** WebProviderTestResult */
    WebProviderTestResult: {
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
  get_agent_revisions_agent_revision_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevision"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_api_keys_key_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ApiKey"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_api_keys_key_id_revoke: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ApiKey"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_application_accounts_account_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_application_accounts_account_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_bot_activate: {
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
        "application/json": components["schemas"]["ActivateBotRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_bot_checks: {
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
        "application/json": components["schemas"]["BotCheckRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotCheck"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_checks_latest: {
    parameters: {
      query?: {
        conversation_id?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotCheckHistory"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_conversations: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConversationPage"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_memory_settings: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountMemorySettings"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_application_accounts_account_id_bot_memory_settings: {
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
        "application/json": components["schemas"]["ReplaceMemorySettings"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountMemorySettings"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_replies: {
    parameters: {
      query: {
        run_id: string;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotReplyCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_setup: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotSetup"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_summary: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotSummary"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_bot_tests: {
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
        "application/json": components["schemas"]["CreateBotTest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotTest"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_tests_latest: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotTestHistory"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_tests_test_id: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        test_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotTestHistory"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_bot_threads: {
    parameters: {
      query?: {
        target_id?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotThreadCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_application_accounts_account_id_credentials: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_event_connection: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EventConnectionStatus"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_memory_scopes: {
    parameters: {
      query: {
        provider_id: string;
        limit?: number;
        cursor?: string | null;
        target_id?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ScopeCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_memory_scopes: {
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
        "application/json": components["schemas"]["ConfigureScope"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Scope"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_memory_scopes_scope_id_documents: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        activity_date?: string | null;
        kind?: ("daily" | "long_term") | null;
        include_shared?: boolean;
      };
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DocumentCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_memory_scopes_scope_id_documents: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        account_id: string;
        scope_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateDocument"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Document"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_memory_scopes_scope_id_documents_search: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SearchDocuments"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DocumentCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_memory_scopes_scope_id_documents_document_id: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
        document_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Document"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_application_accounts_account_id_memory_scopes_scope_id_documents_document_id: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
        document_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_memory_scopes_scope_id_index: {
    parameters: {
      query?: {
        cursor?: string | null;
      };
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryIndex"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_memory_scopes_scope_id_operations: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DocumentCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_memory_scopes_scope_id_operations_document_id: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
        document_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DocumentEntry"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_memory_scopes_scope_id_operations_document_id_reconcile: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        account_id: string;
        scope_id: string;
        document_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DocumentEntry"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_targets: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TargetCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_targets: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountTarget"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_application_accounts_account_id_targets_target_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountTarget"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_application_accounts_account_id_targets_target_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountTarget"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_application_accounts_account_id_targets_target_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_application_accounts_account_id_action: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_assets_asset_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Asset"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_assets_asset_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_assets_asset_id_content: {
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
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/octet-stream": Binary;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_auth_configuration: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthConfiguration"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_auth_context: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CredentialContext"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_auth_csrf: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": {
            [key: string]: string;
          };
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_auth_login: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_auth_logout: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_auth_password_reset: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_auth_password_reset_complete: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_configuration_drafts_draft_id: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        draft_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationDraftReview"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_configuration_drafts_draft_id: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        "If-Match": string;
      };
      path: {
        draft_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateConfigurationDraftRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationDraft"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_configuration_drafts_draft_id_applications: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        draft_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationApplicationCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_configuration_drafts_draft_id_apply: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        "If-Match": string;
      };
      path: {
        draft_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ApplyDraftRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationApplicationReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_configuration_drafts_draft_id_discard: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        "If-Match": string;
      };
      path: {
        draft_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DiscardDraftRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationDraft"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_configuration_drafts_draft_id_rebase: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        "If-Match": string;
      };
      path: {
        draft_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RebaseDraftRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationDraft"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_configuration_sessions_session_id: {
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
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationSessionView"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_configuration_sessions_session_id_threads: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationThreadCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_configuration_sessions_session_id_threads: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
      };
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CreateConfigurationThreadRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationThreadView"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_configuration_threads_thread_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationThreadView"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_configuration_threads_thread_id_inputs: {
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
        "application/json": components["schemas"]["ConfigurationInputRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_connection_authorizations_authorization_id: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        authorization_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Authorization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connection_authorizations_authorization_id_cancel: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        authorization_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Authorization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connection_authorizations_authorization_id_complete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        authorization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["CompleteAuthorizationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Authorization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connection_authorizations_authorization_id_launch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        authorization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LaunchAuthorizationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationRedirect"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connection_authorizations_authorization_id_receive: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        authorization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ReceiveAuthorizationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationRedirect"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_connections_connection_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_connections_connection_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionCleanupReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_connections_connection_id: {
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
        "application/json": components["schemas"]["UpdateConnectionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_authorizations: {
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
        "application/json": components["schemas"]["CreateAuthorizationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Authorization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_check: {
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
        "application/json": components["schemas"]["ConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_connector_revoke: {
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
        "application/json": components["schemas"]["ConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionCleanupReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_disable: {
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
        "application/json": components["schemas"]["ConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_enable: {
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
        "application/json": components["schemas"]["ConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_mcp_discover: {
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
        "application/json": components["schemas"]["ConnectionCommandRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPToolCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_connections_connection_id_mcp_oauth_client: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json":
            components["schemas"]["MCPOAuthClientConfiguration"] | null;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_connections_connection_id_mcp_oauth_client: {
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
        "application/json": components["schemas"]["ConfigureMCPOAuthClientRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_mcp_oauth_discovery: {
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
        "application/json": components["schemas"]["MCPOAuthSetupRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPOAuthDiscovery"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connections_connection_id_mcp_oauth_setup: {
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
        "application/json": components["schemas"]["MCPOAuthSetupRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPOAuthSetup"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_connector_provider_types: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderDefinitionCollection"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_connector_providers_connector_provider_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_connector_providers_connector_provider_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_connector_providers_connector_provider_id_connectors_connector_key: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connector"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_connector_providers_connector_provider_id_connectors_connector_key_tools: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorToolPage"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connector_providers_connector_provider_id_credentials: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connector_providers_connector_provider_id_discover_connectors: {
    parameters: {
      query?: {
        query?: string;
        cursor?: string | null;
        limit?: number;
        refresh?: boolean;
      };
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connector_providers_connector_provider_id_test: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderTestResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_connector_providers_connector_provider_id_action: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_commands_command_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentCommand"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_provider_types: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentProviderDefinition_"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_provider_types_provider_type: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProviderDefinition"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_environment_providers_provider_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_environment_providers_provider_id_credential: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_providers_resource_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_template_revisions_revision_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplateRevision"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_templates_resource_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_environment_templates_template_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_templates_template_id_labels: {
    parameters: {
      query?: never;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_environment_templates_template_id_labels: {
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
        "application/json": components["schemas"]["LabelsBody"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environment_templates_template_id_revisions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentTemplateRevision_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_environment_templates_template_id_revisions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplateRevision"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_environments_environment_id: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UpdateEnvironmentRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Environment"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environments_environment_id_connection: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ClientConnectionStatus"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_environments_environment_id_connection_tickets: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ClientConnectionTicket"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_environments_environment_id_delete: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentCommand"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environments_environment_id_labels: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_environments_environment_id_labels: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LabelsBody"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_environments_environment_id_stop: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentCommand"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_environments_resource_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Environment"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_hook_subscriptions_subscription_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_hook_subscriptions_subscription_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_hook_subscriptions_subscription_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_hook_subscriptions_subscription_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_hook_subscriptions_subscription_id_deliveries_delivery_id_redrive: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_invitations_invitation_id_accept: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_invitations_invitation_id_resend: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationDelivery"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_invitations_invitation_id_revoke: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Invitation"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_mcp_servers: {
    parameters: {
      query?: {
        query?: string;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPServerCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_mcp_servers_server_key: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        server_key: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MCPServer"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_memory_provider_types: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProviderDefinitionCollection"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_memory_provider_types_provider_type: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProviderDefinition"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_model_provider_types: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProviderDefinitionCollection"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Organization_"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_organizations_organization: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_connector_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_connector_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_environment_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentProvider_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_environment_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_environment_templates: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentTemplate_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_environment_templates: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_organizations_organization_icon: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_organizations_organization_icon: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Organization"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_icon_image_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "image/webp": Binary;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_invitations: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Invitation_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_invitations: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationDelivery"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_memory_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_memory_providers: {
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
        "application/json": components["schemas"]["CreateMemoryProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_memory_providers_provider_id: {
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
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_organizations_organization_memory_providers_provider_id: {
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
        "application/json": components["schemas"]["UpdateMemoryProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_memory_providers_provider_id_references: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProviderReferenceCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_model_catalog: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelCatalogCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_model_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_model_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_model_providers_provider_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_organizations_organization_model_providers_provider_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_model_providers_provider_id_test: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_models: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_models: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_models_model_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_organizations_organization_models_model_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_models_model_id_test: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_permissions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["OrganizationPermissions"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_role_bindings: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_RoleBinding_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_role_bindings: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_security_audit_events: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_SecurityEvent_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_users: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_User_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_web_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_web_providers: {
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
        "application/json": components["schemas"]["CreateWebProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_web_providers_provider_id: {
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
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_organizations_organization_web_providers_provider_id: {
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
        "application/json": components["schemas"]["UpdateWebProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_web_providers_provider_id_references: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderReferenceCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_web_providers_provider_id_test: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderTestResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_organizations_organization_workspaces: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Workspace_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_organizations_organization_workspaces: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_queued_submissions_queued_submission_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmission"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_queued_submissions_queued_submission_id: {
    parameters: {
      query: {
        expected_version: number;
      };
      header: {
        "Idempotency-Key": string;
      };
      path: {
        queued_submission_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_queued_submissions_queued_submission_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmissionMutationReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_role_bindings_binding_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_role_bindings_binding_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_role_bindings_binding_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_run_attempts_run_attempt_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAttemptResource"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_run_attempts_run_attempt_id_events: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ResourceLifecycleEventPage"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunResource"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_attempts: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAttemptCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_events: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ResourceLifecycleEventPage"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_runs_run_id_feedback: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_runs_run_id_fork: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_runs_run_id_interrupt: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InterruptReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_items: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ItemCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_labels: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_runs_run_id_labels: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LabelsBody"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_lineage: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunLineage"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_pending_actions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["PendingActionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_runs_run_id_retry: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_runs_run_id_steer: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SteerReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_steers_steer_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SteerStatus"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_runs_run_id_stream: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "text/event-stream": string;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_runs_source_run_id_continue: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_service_accounts_account_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ServiceAccount"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_service_accounts_account_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_service_accounts_account_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ServiceAccount"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_service_accounts_account_id_api_keys: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ApiKey_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_service_accounts_account_id_api_keys: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CreatedKey"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_sessions_session_id_labels: {
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
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_sessions_session_id_labels: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LabelsBody"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_sessions_session_id_threads: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_skill_revisions_skill_revision_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillRevision"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_skill_revisions_skill_revision_id_content: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_skill_uploads_upload_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillUploadReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_skill_uploads_upload_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_skills_skill_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Skill"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_skills_skill_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_skills_skill_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Skill"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_skills_skill_id_labels: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_skills_skill_id_labels: {
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
        "application/json": components["schemas"]["LabelsBody"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_skills_skill_id_references: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillAgentReferenceCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_skills_skill_id_revisions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillRevisionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_skills_skill_id_revisions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillPublicationReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_threads_thread_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadResource"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_threads_thread_id_labels: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_threads_thread_id_labels: {
    parameters: {
      query?: never;
      header: {
        "If-Match": string;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LabelsBody"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_threads_thread_id_queued_submissions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmissionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_threads_thread_id_queued_submissions_consume: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueuedSubmissionConsumptionReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_threads_thread_id_queued_submissions_reorder: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadQueueMutationReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_threads_thread_id_runs: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_threads_thread_id_runs: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadRunSubmissionReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_users_me: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_users_me: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_users_me_auth_sessions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_AuthSession_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_users_me_auth_sessions_session_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_users_me_avatar: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_users_me_avatar: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["User"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_users_me_email_change: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_users_me_email_change_complete: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_users_me_password: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_users_me_security_activity: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_SecurityEvent_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_users_user_id_avatar_image_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "image/webp": Binary;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_web_provider_types: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderDefinitionCollection"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_web_provider_types_provider_type: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderDefinition"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_workspaces_workspace: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_workspaces_workspace: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_agents: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        enabled?: boolean | null;
        source?: components["schemas"]["AgentSource"] | null;
        include_archived?: boolean;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_agents: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCreateResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_agents_agent: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_workspaces_workspace_agents_agent: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_workspaces_workspace_agents_agent_avatar: {
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
        "application/octet-stream": Binary;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_workspaces_workspace_agents_agent_avatar: {
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
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_agents_agent_avatar_image_id: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        image_id: string;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": unknown;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_agents_agent_duplicate: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_agents_agent_labels: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_workspaces_workspace_agents_agent_labels: {
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
        "application/json": components["schemas"]["LabelsBody"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LabelsBody"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_agents_agent_revisions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_agents_agent_revisions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCreateResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_agents_agent_revisions_revision_id_restore: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevisionCreateResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_agents_agent_action: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Agent"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_api_keys: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ApiKey_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_application_account_provider_types: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountProviderDefinitionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_application_accounts: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AccountCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_application_accounts: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Account"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_assets: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssetCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_assets: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Asset"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_bots: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        platform?: ("slack" | "lark") | null;
        condition?:
          | (
              | "disabled"
              | "needs_verification"
              | "check_failed"
              | "reception_off"
              | "receiving"
            )
          | null;
        search?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["BotCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_bots_feishu_installation: {
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
        "application/json": components["schemas"]["DiscoverFeishuInstallationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InstallationInfo"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_configuration_assistant_readiness: {
    parameters: {
      query?: {
        target_agent_id?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AssistantReadiness"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_configuration_sessions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationSessionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_configuration_sessions: {
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
        "application/json": components["schemas"]["CreateSessionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConfigurationSessionView"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_connections: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_connections: {
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
        "application/json": components["schemas"]["CreateConnectionRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_connector_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_connector_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ConnectorProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_environment_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentProvider_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_environment_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_environment_templates: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_EnvironmentTemplate_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_environment_templates: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentTemplate"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_environments: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Collection_Environment_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_environments: {
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
          | components["schemas"]["CreateManagedEnvironmentRequest"]
          | components["schemas"]["RegisterEnvironmentRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Environment"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_events: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WorkspaceEventPage"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_hook_subscriptions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscriptionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_hook_subscriptions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["HookSubscription"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_workspaces_workspace_icon: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_workspaces_workspace_icon: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Workspace"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_icon_image_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "image/webp": Binary;
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_invitations: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_Invitation_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_invitations: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationDelivery"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_members: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_User_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_memory_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_memory_providers: {
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
        "application/json": components["schemas"]["CreateMemoryProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_memory_providers_provider_id: {
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
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_workspaces_workspace_memory_providers_provider_id: {
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
        "application/json": components["schemas"]["UpdateMemoryProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_memory_providers_provider_id_memories: {
    parameters: {
      query: {
        /** @description Maximum loaded records, not a total count. */
        limit?: number;
        cursor?: string | null;
        scope: components["schemas"]["MemoryScope"];
        subject_id?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_memory_providers_provider_id_memories: {
    parameters: {
      query: {
        scope: components["schemas"]["MemoryScope"];
        subject_id?: string | null;
      };
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryWrite"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Memory"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_memory_providers_provider_id_memories_search: {
    parameters: {
      query: {
        scope: components["schemas"]["MemoryScope"];
        subject_id?: string | null;
      };
      header?: never;
      path: {
        provider_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemorySearch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_memory_providers_provider_id_memories_memory_id: {
    parameters: {
      query: {
        scope: components["schemas"]["MemoryScope"];
        subject_id?: string | null;
      };
      header?: never;
      path: {
        provider_id: string;
        memory_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Memory"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  put_workspaces_workspace_memory_providers_provider_id_memories_memory_id: {
    parameters: {
      query: {
        scope: components["schemas"]["MemoryScope"];
        subject_id?: string | null;
      };
      header?: never;
      path: {
        provider_id: string;
        memory_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryWrite"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Memory"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  delete_workspaces_workspace_memory_providers_provider_id_memories_memory_id: {
    parameters: {
      query: {
        scope: components["schemas"]["MemoryScope"];
        subject_id?: string | null;
      };
      header?: never;
      path: {
        provider_id: string;
        memory_id: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      204: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content?: never;
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_memory_providers_provider_id_memory_access: {
    parameters: {
      query: {
        scope: components["schemas"]["MemoryScope"];
        subject_id?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryAccess"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_memory_providers_provider_id_references: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryProviderReferenceCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_model_catalog: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelCatalogCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_model_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_model_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_model_providers_provider_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_workspaces_workspace_model_providers_provider_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_model_providers_provider_id_test: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_models: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        query?: string | null;
        provider_id?: string | null;
        enabled?: boolean | null;
        scope?: ("organization" | "workspace") | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_models: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_models_model_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_workspaces_workspace_models_model_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Model"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_models_model_id_test: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ModelConnectionTestResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_permissions: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Permissions"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_personal_api_keys: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ApiKey_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_personal_api_keys: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CreatedKey"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_role_bindings: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_RoleBinding_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_role_bindings: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RoleBinding"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_runs: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_runs: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunAcceptanceReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_security_audit_events: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_SecurityEvent_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_service_accounts: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Page_ServiceAccount_"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_service_accounts: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ServiceAccount"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_sessions: {
    parameters: {
      query?: {
        q?: string | null;
        agent_id?: string | null;
        status?: components["schemas"]["RunStatus"][];
        trigger_type?: string[];
        updated_after?: string | null;
        updated_before?: string | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_skill_uploads: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillUploadReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_skills: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        q?: string | null;
        source_kind?: ("zip" | "github") | null;
        label?: string[];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_skills: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillPublicationReceipt"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_skills_skill_key: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        skill_key: string;
        workspace: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Skill"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_threads: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Thread"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_toolsets: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ToolsetCatalog"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_toolsets_validate: {
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
        "application/json": components["schemas"]["ToolsetCandidate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ToolsetCandidateResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_trace_query: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TraceQueryDescriptor"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_traces: {
    parameters: {
      query?: {
        from?: string | null;
        to?: string | null;
        limit?: number;
        cursor?: string | null;
        query?: string | null;
        search_in?: components["schemas"]["SearchIn"] | null;
        session_id?: string | null;
        thread_id?: string | null;
        run_id?: string | null;
        run_attempt_id?: string | null;
        metadata?: string[] | null;
        view?: components["schemas"]["TraceView"];
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["TraceCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_traces_trace_id: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Trace"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_traces_trace_id_observations: {
    parameters: {
      query?: {
        view?: components["schemas"]["TraceView"];
        limit?: number;
        cursor?: string | null;
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ObservationCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_web_providers: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_web_providers: {
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
        "application/json": components["schemas"]["CreateWebProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_web_providers_provider_id: {
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
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  patch_workspaces_workspace_web_providers_provider_id: {
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
        "application/json": components["schemas"]["UpdateWebProviderRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          /** @description Strong representation precondition. */
          ETag?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProvider"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  get_workspaces_workspace_web_providers_provider_id_references: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderReferenceCollection"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
  post_workspaces_workspace_web_providers_provider_id_test: {
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
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["WebProviderTestResult"];
        };
      };
      /** @description Invalid request. */
      400: {
        headers: {
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
      /** @description Service error. */
      default: {
        headers: {
          "Retry-After"?: string;
          "X-Request-ID"?: string;
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ErrorResponse"];
        };
      };
    };
  };
}

export type Binary = Blob | ReadableStream<Uint8Array>;
