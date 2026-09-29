export interface paths {
  "/api/v1/agent-composer": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Prepare Composer
     * @description The workspace's Agent Composer, created or brought up to date with the deployment's definition.
     *
     *     Refused with `model_required` while the workspace has no model the caller can use.
     */
    post: operations["prepare_composer_api_v1_agent_composer_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Agents
     * @description Agents of the workspace. `q` matches the name or description, ignoring case; `archived` keeps only
     *     archived agents, or only open ones; `source=builtin` finds the Agent Composer; the skill filters keep those
     *     with a revision pinning that skill or that skill revision.
     */
    get: operations["list_agents_api_v1_agents_get"];
    put?: never;
    /** Create Agent */
    post: operations["create_agent_api_v1_agents_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/validate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Validate Revision
     * @description No content when creating a revision of the configuration would accept it, else the same `invalid_argument`
     *     error with the field's path relative to `config`; nothing is stored.
     */
    post: operations["validate_revision_api_v1_agents_validate_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Agent */
    get: operations["get_agent_api_v1_agents__agent_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Agent */
    patch: operations["update_agent_api_v1_agents__agent_id__patch"];
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/archive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Archive Agent */
    post: operations["archive_agent_api_v1_agents__agent_id__archive_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/avatar": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Avatar */
    get: operations["get_avatar_api_v1_agents__agent_id__avatar_get"];
    /** Put Avatar */
    put: operations["put_avatar_api_v1_agents__agent_id__avatar_put"];
    post?: never;
    /** Delete Avatar */
    delete: operations["delete_avatar_api_v1_agents__agent_id__avatar_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/duplicate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Duplicate Agent */
    post: operations["duplicate_agent_api_v1_agents__agent_id__duplicate_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/revisions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Revisions */
    get: operations["list_revisions_api_v1_agents__agent_id__revisions_get"];
    put?: never;
    /**
     * Create Revision
     * @description A configuration that validates to the default revision's creates nothing and returns that revision.
     */
    post: operations["create_revision_api_v1_agents__agent_id__revisions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/revisions/{revision_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Revision */
    get: operations["get_revision_api_v1_agents__agent_id__revisions__revision_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/revisions/{revision_id}/set-default": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set Default */
    post: operations["set_default_api_v1_agents__agent_id__revisions__revision_id__set_default_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/unarchive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Unarchive Agent */
    post: operations["unarchive_agent_api_v1_agents__agent_id__unarchive_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/assets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Assets */
    get: operations["list_assets_api_v1_assets_get"];
    put?: never;
    /** Create Asset */
    post: operations["create_asset_api_v1_assets_post"];
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
    /** Retire Asset */
    delete: operations["retire_asset_api_v1_assets__asset_id__delete"];
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
    /** Read Asset Content */
    get: operations["read_asset_content_api_v1_assets__asset_id__content_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/bootstrap": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Bootstrap Administrator
     * @description Public only until initialized: creates the first administrator, as the `bootstrap` command does, signed in.
     */
    post: operations["bootstrap_administrator_api_v1_auth_bootstrap_post"];
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
  "/api/v1/auth/email-change/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Confirm Email Change */
    post: operations["confirm_email_change_api_v1_auth_email_change_confirm_post"];
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
    /** Password Login */
    post: operations["password_login_api_v1_auth_login_post"];
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
  "/api/v1/auth/password-reset/confirm": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Confirm Password Reset */
    post: operations["confirm_password_reset_api_v1_auth_password_reset_confirm_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/auth/session": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Session Profile
     * @description Restores a browser session; the CSRF token is stable for the session's lifetime.
     */
    get: operations["session_profile_api_v1_auth_session_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Connections */
    get: operations["list_connections_api_v1_connections_get"];
    put?: never;
    /** Create Connection */
    post: operations["create_connection_api_v1_connections_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/callback": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Complete Authorization
     * @description Public: the one-use state authenticates the browser that the authorization server sends back, and the flow
     *     cookie the browser that started the flow.
     *
     *     Attempts per client address share the login flows' bound.
     */
    get: operations["complete_authorization_api_v1_connections_callback_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/redirect-uri": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Redirect Uri
     * @description The deployment's callback, for registering an OAuth client in advance; the same for every connection.
     */
    get: operations["get_redirect_uri_api_v1_connections_redirect_uri_get"];
    put?: never;
    post?: never;
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
    get: operations["get_connection_api_v1_connections__connection_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Connection */
    patch: operations["update_connection_api_v1_connections__connection_id__patch"];
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/authorize": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Authorize Connection
     * @description A browser flow needs a login session and is bound to this browser by a cookie the callback checks; an API
     *     key authorizes only a client-credentials client, without a browser.
     */
    post: operations["authorize_connection_api_v1_connections__connection_id__authorize_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Connection */
    post: operations["revoke_connection_api_v1_connections__connection_id__revoke_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Connection */
    post: operations["test_connection_api_v1_connections__connection_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/tools": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Tools */
    get: operations["list_tools_api_v1_connections__connection_id__tools_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Providers */
    get: operations["list_providers_api_v1_connector_providers_get"];
    put?: never;
    /** Create Provider */
    post: operations["create_provider_api_v1_connector_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Provider */
    get: operations["get_provider_api_v1_connector_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Provider
     * @description A `config` change must also replace or remove a stored credential: it never follows a new endpoint.
     */
    patch: operations["update_provider_api_v1_connector_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}/apps": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Apps */
    get: operations["list_apps_api_v1_connector_providers__provider_id__apps_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}/apps/{app}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get App */
    get: operations["get_app_api_v1_connector_providers__provider_id__apps__app__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}/apps/{app}/actions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Actions */
    get: operations["list_actions_api_v1_connector_providers__provider_id__apps__app__actions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Provider */
    post: operations["test_provider_api_v1_connector_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Providers */
    get: operations["list_providers_api_v1_environment_providers_get"];
    put?: never;
    /** Create Provider */
    post: operations["create_provider_api_v1_environment_providers_post"];
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
    /** Get Provider */
    get: operations["get_provider_api_v1_environment_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Provider
     * @description A `config` change must also replace or remove a stored credential: it never follows a new endpoint.
     */
    patch: operations["update_provider_api_v1_environment_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/environment-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Provider */
    post: operations["test_provider_api_v1_environment_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-templates": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Templates */
    get: operations["list_templates_api_v1_environment_templates_get"];
    put?: never;
    /** Create Template */
    post: operations["create_template_api_v1_environment_templates_post"];
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
    /** Get Template */
    get: operations["get_template_api_v1_environment_templates__template_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Template */
    patch: operations["update_template_api_v1_environment_templates__template_id__patch"];
    trace?: never;
  };
  "/api/v1/environments": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Environments */
    get: operations["list_environments_api_v1_environments_get"];
    put?: never;
    /**
     * Create Environment
     * @description Reserve a managed sandbox from a template (`creating`), or register an external envd target (`ready`).
     */
    post: operations["create_environment_api_v1_environments_post"];
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
    /** Get Environment */
    get: operations["get_environment_api_v1_environments__environment_id__get"];
    put?: never;
    post?: never;
    /** Delete Environment */
    delete: operations["delete_environment_api_v1_environments__environment_id__delete"];
    options?: never;
    head?: never;
    /** Update Environment */
    patch: operations["update_environment_api_v1_environments__environment_id__patch"];
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
  "/api/v1/invitations/{invitation_id}/accept": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Accept
     * @description Public by token: creates or joins the invited account and starts a login session.
     */
    post: operations["accept_api_v1_invitations__invitation_id__accept_post"];
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
    /**
     * List Mcp Servers
     * @description Suggested Remote MCP servers, packaged and from the deployment; readable by every signed-in principal.
     */
    get: operations["list_mcp_servers_api_v1_mcp_servers_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/media-understanding-defaults": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Media Defaults */
    get: operations["get_media_defaults_api_v1_media_understanding_defaults_get"];
    /**
     * Replace Media Defaults
     * @description Replaces all three kinds; each model must declare it understands its kind. Requires workspace admin.
     */
    put: operations["replace_media_defaults_api_v1_media_understanding_defaults_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Memories */
    get: operations["list_memories_api_v1_memories_get"];
    put?: never;
    /** Create Memory */
    post: operations["create_memory_api_v1_memories_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Memory */
    get: operations["get_memory_api_v1_memories__memory_id__get"];
    put?: never;
    post?: never;
    /** Delete Memory */
    delete: operations["delete_memory_api_v1_memories__memory_id__delete"];
    options?: never;
    head?: never;
    /** Update Memory */
    patch: operations["update_memory_api_v1_memories__memory_id__patch"];
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/files": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Files */
    get: operations["list_files_api_v1_memories__memory_id__files_get"];
    put?: never;
    /** Create File */
    post: operations["create_file_api_v1_memories__memory_id__files_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/files/move": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Move File
     * @description Move the source file `If-Match` names; the destination must be free.
     */
    post: operations["move_file_api_v1_memories__memory_id__files_move_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/files/{path}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Read File */
    get: operations["read_file_api_v1_memories__memory_id__files__path__get"];
    /** Replace File */
    put: operations["replace_file_api_v1_memories__memory_id__files__path__put"];
    post?: never;
    /** Delete File */
    delete: operations["delete_file_api_v1_memories__memory_id__files__path__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/records": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Records */
    get: operations["list_records_api_v1_memories__memory_id__records_get"];
    put?: never;
    /** Add Record */
    post: operations["add_record_api_v1_memories__memory_id__records_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/records/search": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Search Records
     * @description The records most similar to the query; the query travels in the body, never the URL.
     */
    post: operations["search_records_api_v1_memories__memory_id__records_search_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/records/{record_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /**
     * Update Record
     * @description Replace the record's text; records carry no version, so the last writer wins.
     */
    put: operations["update_record_api_v1_memories__memory_id__records__record_id__put"];
    post?: never;
    /** Delete Record */
    delete: operations["delete_record_api_v1_memories__memory_id__records__record_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/revisions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Revisions */
    get: operations["list_revisions_api_v1_memories__memory_id__revisions_get"];
    put?: never;
    post?: never;
    /**
     * Purge History
     * @description Delete every retained revision of one file path.
     */
    delete: operations["purge_history_api_v1_memories__memory_id__revisions_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/revisions/{seq}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Revision */
    get: operations["get_revision_api_v1_memories__memory_id__revisions__seq__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memories/{memory_id}/revisions/{seq}/restore": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Restore Revision
     * @description Set the path back to the content the change replaced; `If-Match` names the file there, if any.
     */
    post: operations["restore_revision_api_v1_memories__memory_id__revisions__seq__restore_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Providers */
    get: operations["list_providers_api_v1_memory_providers_get"];
    put?: never;
    /** Create Provider */
    post: operations["create_provider_api_v1_memory_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Provider */
    get: operations["get_provider_api_v1_memory_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Provider
     * @description A `config` change must also replace or remove a stored credential: it never follows a new endpoint.
     */
    patch: operations["update_provider_api_v1_memory_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/memory-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Provider */
    post: operations["test_provider_api_v1_memory_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/model-catalog": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Model Catalog
     * @description The models.dev models the registered model provider types serve, for any signed-in principal.
     */
    get: operations["get_model_catalog_api_v1_model_catalog_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/model-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Providers */
    get: operations["list_providers_api_v1_model_providers_get"];
    put?: never;
    /** Create Provider */
    post: operations["create_provider_api_v1_model_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/model-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Provider */
    get: operations["get_provider_api_v1_model_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Provider
     * @description A `config` change must also replace or remove a stored credential: it never follows a new endpoint.
     */
    patch: operations["update_provider_api_v1_model_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/model-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Provider */
    post: operations["test_provider_api_v1_model_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Models */
    get: operations["list_models_api_v1_models_get"];
    put?: never;
    /**
     * Create Model
     * @description Needs `write` on the workspace and on the model's provider, whose credential the model spends.
     */
    post: operations["create_model_api_v1_models_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/models/{key}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Model */
    get: operations["get_model_api_v1_models__key__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Model
     * @description A configuration change also needs `write` on the model's provider.
     */
    patch: operations["update_model_api_v1_models__key__patch"];
    trace?: never;
  };
  "/api/v1/organizations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organizations */
    get: operations["list_organizations_api_v1_organizations_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Organization */
    get: operations["get_organization_api_v1_organizations__organization_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Organization */
    patch: operations["update_organization_api_v1_organizations__organization_id__patch"];
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/audit-events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organization Audit Events */
    get: operations["list_organization_audit_events_api_v1_organizations__organization_id__audit_events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/grants": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organization Grants */
    get: operations["list_organization_grants_api_v1_organizations__organization_id__grants_get"];
    put?: never;
    /** Create Organization Grant */
    post: operations["create_organization_grant_api_v1_organizations__organization_id__grants_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/grants/{grant_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Delete Organization Grant */
    delete: operations["delete_organization_grant_api_v1_organizations__organization_id__grants__grant_id__delete"];
    options?: never;
    head?: never;
    /**
     * Change Organization Grant
     * @description The grant is replaced: the result carries its new ID.
     */
    patch: operations["change_organization_grant_api_v1_organizations__organization_id__grants__grant_id__patch"];
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/icon": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Organization Icon */
    get: operations["get_organization_icon_api_v1_organizations__organization_id__icon_get"];
    /** Put Organization Icon */
    put: operations["put_organization_icon_api_v1_organizations__organization_id__icon_put"];
    post?: never;
    /** Delete Organization Icon */
    delete: operations["delete_organization_icon_api_v1_organizations__organization_id__icon_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/invitations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organization Invitations */
    get: operations["list_organization_invitations_api_v1_organizations__organization_id__invitations_get"];
    put?: never;
    /** Create Organization Invitation */
    post: operations["create_organization_invitation_api_v1_organizations__organization_id__invitations_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/invitations/{invitation_id}/resend": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Resend Organization Invitation */
    post: operations["resend_organization_invitation_api_v1_organizations__organization_id__invitations__invitation_id__resend_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/invitations/{invitation_id}/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Organization Invitation */
    post: operations["revoke_organization_invitation_api_v1_organizations__organization_id__invitations__invitation_id__revoke_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/members": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Members */
    get: operations["list_members_api_v1_organizations__organization_id__members_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/organizations/{organization_id}/workspaces": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Organization Workspaces */
    get: operations["list_organization_workspaces_api_v1_organizations__organization_id__workspaces_get"];
    put?: never;
    /** Create Workspace */
    post: operations["create_workspace_api_v1_organizations__organization_id__workspaces_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/provider-types/{kind}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Provider Types */
    get: operations["list_provider_types_api_v1_provider_types__kind__get"];
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
    /**
     * Update Run
     * @description Labels only.
     */
    patch: operations["update_run_api_v1_runs__run_id__patch"];
    trace?: never;
  };
  "/api/v1/runs/{run_id}/attempts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Run Attempts */
    get: operations["run_attempts_api_v1_runs__run_id__attempts_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/attempts/{attempt_id}/trace": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Attempt Spans
     * @description The attempt's spans, including its inline child runs.
     */
    get: operations["list_attempt_spans_api_v1_runs__run_id__attempts__attempt_id__trace_get"];
    put?: never;
    post?: never;
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
    /**
     * Fork Run
     * @description A new thread in the run's session that continues from this run's committed history.
     */
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
    /** Run Items */
    get: operations["run_items_api_v1_runs__run_id__items_get"];
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
    /**
     * Run Lineage
     * @description The run and its ancestors, nearest first, across fork origins.
     */
    get: operations["run_lineage_api_v1_runs__run_id__lineage_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/resume": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Resume Run
     * @description Answer the exact waiting run's approvals, client tools and user questions; the successor continues from them.
     */
    post: operations["resume_run_api_v1_runs__run_id__resume_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Sessions */
    get: operations["list_sessions_api_v1_sessions_get"];
    put?: never;
    /** Create Session */
    post: operations["create_session_api_v1_sessions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/sessions/{session_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Session */
    get: operations["get_session_api_v1_sessions__session_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Session */
    patch: operations["update_session_api_v1_sessions__session_id__patch"];
    trace?: never;
  };
  "/api/v1/skills": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Skills
     * @description Skills of the workspace. `q` matches the name or description, ignoring case; `source` the kind of
     *     source the default revision was read from; `archived` keeps only archived skills, or only open ones.
     */
    get: operations["list_skills_api_v1_skills_get"];
    put?: never;
    /** Create Skill */
    post: operations["create_skill_api_v1_skills_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/validate": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Validate Package
     * @description The manifest the package would give a new skill or revision, checked as creation checks it; nothing is
     *     stored.
     */
    post: operations["validate_package_api_v1_skills_validate_post"];
    delete?: never;
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
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Skill
     * @description Name, description and labels; an archived skill changes only by unarchiving.
     */
    patch: operations["update_skill_api_v1_skills__skill_id__patch"];
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/archive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Archive Skill
     * @description Archived skills keep their revisions readable and pinned; they refuse new revisions and new pins.
     */
    post: operations["archive_skill_api_v1_skills__skill_id__archive_post"];
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
    /** List Revisions */
    get: operations["list_revisions_api_v1_skills__skill_id__revisions_get"];
    put?: never;
    /**
     * Create Revision
     * @description A package whose manifest equals the default revision's creates nothing and returns that revision.
     */
    post: operations["create_revision_api_v1_skills__skill_id__revisions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/revisions/{revision_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Revision */
    get: operations["get_revision_api_v1_skills__skill_id__revisions__revision_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/revisions/{revision_id}/content": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Read Archive
     * @description The revision's package as a zip archive.
     */
    get: operations["read_archive_api_v1_skills__skill_id__revisions__revision_id__content_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/revisions/{revision_id}/files/{path}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Read File
     * @description One package file, by the path the revision's manifest lists.
     */
    get: operations["read_file_api_v1_skills__skill_id__revisions__revision_id__files__path__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/revisions/{revision_id}/set-default": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Set Default Revision */
    post: operations["set_default_revision_api_v1_skills__skill_id__revisions__revision_id__set_default_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/skills/{skill_id}/unarchive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Unarchive Skill */
    post: operations["unarchive_skill_api_v1_skills__skill_id__unarchive_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/subscriptions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Subscriptions */
    get: operations["list_subscriptions_api_v1_subscriptions_get"];
    put?: never;
    /**
     * Create Subscription
     * @description The response is the only time the signing secret is returned.
     */
    post: operations["create_subscription_api_v1_subscriptions_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/subscriptions/{subscription_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Subscription */
    get: operations["get_subscription_api_v1_subscriptions__subscription_id__get"];
    put?: never;
    post?: never;
    /** Delete Subscription */
    delete: operations["delete_subscription_api_v1_subscriptions__subscription_id__delete"];
    options?: never;
    head?: never;
    /** Update Subscription */
    patch: operations["update_subscription_api_v1_subscriptions__subscription_id__patch"];
    trace?: never;
  };
  "/api/v1/subscriptions/{subscription_id}/deliveries": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Deliveries */
    get: operations["list_deliveries_api_v1_subscriptions__subscription_id__deliveries_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/subscriptions/{subscription_id}/deliveries/{delivery_id}/redeliver": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Redeliver */
    post: operations["redeliver_api_v1_subscriptions__subscription_id__deliveries__delivery_id__redeliver_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Threads */
    get: operations["list_threads_api_v1_threads_get"];
    put?: never;
    /**
     * Create Thread
     * @description Create a thread (and its session unless one is named) with its first message.
     */
    post: operations["create_thread_api_v1_threads_post"];
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
    /** Update Thread */
    patch: operations["update_thread_api_v1_threads__thread_id__patch"];
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/archive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Archive Thread */
    post: operations["archive_thread_api_v1_threads__thread_id__archive_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/environments": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Mounts */
    get: operations["list_mounts_api_v1_threads__thread_id__environments_get"];
    put?: never;
    /** Add Mount */
    post: operations["add_mount_api_v1_threads__thread_id__environments_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/environments/{name}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Remove Mount */
    delete: operations["remove_mount_api_v1_threads__thread_id__environments__name__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/inbox": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Inbox
     * @description In inbox order.
     */
    get: operations["list_inbox_api_v1_threads__thread_id__inbox_get"];
    put?: never;
    /**
     * Submit Message
     * @description Append a message; it starts a run at once when the thread can accept it, or steers the active run.
     */
    post: operations["submit_message_api_v1_threads__thread_id__inbox_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/inbox/order": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Reorder Inbox */
    put: operations["reorder_inbox_api_v1_threads__thread_id__inbox_order_put"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/inbox/{entry_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Entry
     * @description One entry and its disposition; edits name the thread's ETag.
     */
    get: operations["get_entry_api_v1_threads__thread_id__inbox__entry_id__get"];
    put?: never;
    post?: never;
    /**
     * Withdraw Entry
     * @description Withdraw a pending entry; its tombstone keeps the request key.
     */
    delete: operations["withdraw_entry_api_v1_threads__thread_id__inbox__entry_id__delete"];
    options?: never;
    head?: never;
    /** Edit Entry */
    patch: operations["edit_entry_api_v1_threads__thread_id__inbox__entry_id__patch"];
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/memories": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Mounts */
    get: operations["list_mounts_api_v1_threads__thread_id__memories_get"];
    put?: never;
    /** Add Mount */
    post: operations["add_mount_api_v1_threads__thread_id__memories_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/memories/{name}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Remove Mount */
    delete: operations["remove_mount_api_v1_threads__thread_id__memories__name__delete"];
    options?: never;
    head?: never;
    /** Update Mount */
    patch: operations["update_mount_api_v1_threads__thread_id__memories__name__patch"];
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/runs": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Thread Runs
     * @description Newest first.
     */
    get: operations["list_thread_runs_api_v1_threads__thread_id__runs_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/stream": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Thread Stream
     * @description Live output of the thread's runs over SSE: `delta` and `boundary` frames with `changed`, `reset`, `gap`.
     */
    get: operations["thread_stream_api_v1_threads__thread_id__stream_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/toolsets": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Toolsets */
    get: operations["list_toolsets_api_v1_toolsets_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/trace-backend": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Trace Backend
     * @description The backend trace queries read, and how far back they find a trace.
     */
    get: operations["get_trace_backend_api_v1_trace_backend_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/traces": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Traces
     * @description Trace root spans, one per attempt. A cursor keeps the window of the first page.
     */
    get: operations["list_traces_api_v1_traces_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/traces/{trace_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Trace
     * @description The trace's root span.
     */
    get: operations["get_trace_api_v1_traces__trace_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/traces/{trace_id}/spans": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Trace Spans
     * @description The trace's spans.
     */
    get: operations["list_trace_spans_api_v1_traces__trace_id__spans_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/uploads": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Create Upload
     * @description Repeating a request with the same `Idempotency-Key` and bytes returns the same upload.
     */
    post: operations["create_upload_api_v1_uploads_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/usage": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Summarize Usage */
    get: operations["summarize_usage_api_v1_usage_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/usage/agents": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Usage Agents */
    get: operations["usage_agents_api_v1_usage_agents_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/usage/models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Usage Models */
    get: operations["usage_models_api_v1_usage_models_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/usage/overview": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Usage Overview */
    get: operations["usage_overview_api_v1_usage_overview_get"];
    put?: never;
    post?: never;
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
    /** Get Profile */
    get: operations["get_profile_api_v1_users_me_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Profile */
    patch: operations["update_profile_api_v1_users_me_patch"];
    trace?: never;
  };
  "/api/v1/users/me/audit-events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * List Account Audit Events
     * @description The caller's own trail, account-wide events included; requires a login session.
     */
    get: operations["list_account_audit_events_api_v1_users_me_audit_events_get"];
    put?: never;
    post?: never;
    delete?: never;
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
  "/api/v1/users/me/disable": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Disable Account
     * @description Disable the caller's own account, proven by the current password; no route enables it again.
     */
    post: operations["disable_account_api_v1_users_me_disable_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/keys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List User Keys */
    get: operations["list_user_keys_api_v1_users_me_keys_get"];
    put?: never;
    /**
     * Create User Key
     * @description Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation.
     */
    post: operations["create_user_key_api_v1_users_me_keys_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/keys/{key_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Revoke User Key */
    delete: operations["revoke_user_key_api_v1_users_me_keys__key_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/login-sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Login Sessions */
    get: operations["list_login_sessions_api_v1_users_me_login_sessions_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/users/me/login-sessions/{session_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Revoke Login Session */
    delete: operations["revoke_login_session_api_v1_users_me_login_sessions__session_id__delete"];
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
  "/api/v1/users/{user_id}/avatar": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Avatar */
    get: operations["get_avatar_api_v1_users__user_id__avatar_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/web-providers": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Providers */
    get: operations["list_providers_api_v1_web_providers_get"];
    put?: never;
    /** Create Provider */
    post: operations["create_provider_api_v1_web_providers_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/web-providers/{provider_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Provider */
    get: operations["get_provider_api_v1_web_providers__provider_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Provider
     * @description A `config` change must also replace or remove a stored credential: it never follows a new endpoint.
     */
    patch: operations["update_provider_api_v1_web_providers__provider_id__patch"];
    trace?: never;
  };
  "/api/v1/web-providers/{provider_id}/test": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Test Provider */
    post: operations["test_provider_api_v1_web_providers__provider_id__test_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspaces */
    get: operations["list_workspaces_api_v1_workspaces_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Workspace */
    get: operations["get_workspace_api_v1_workspaces__workspace_id__get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Workspace */
    patch: operations["update_workspace_api_v1_workspaces__workspace_id__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/archive": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Archive Workspace */
    post: operations["archive_workspace_api_v1_workspaces__workspace_id__archive_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/audit-events": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Audit Events */
    get: operations["list_workspace_audit_events_api_v1_workspaces__workspace_id__audit_events_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/grants": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Grants */
    get: operations["list_workspace_grants_api_v1_workspaces__workspace_id__grants_get"];
    put?: never;
    /** Create Workspace Grant */
    post: operations["create_workspace_grant_api_v1_workspaces__workspace_id__grants_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/grants/{grant_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Delete Workspace Grant */
    delete: operations["delete_workspace_grant_api_v1_workspaces__workspace_id__grants__grant_id__delete"];
    options?: never;
    head?: never;
    /**
     * Change Workspace Grant
     * @description The grant is replaced: the result carries its new ID.
     */
    patch: operations["change_workspace_grant_api_v1_workspaces__workspace_id__grants__grant_id__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/icon": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Workspace Icon */
    get: operations["get_workspace_icon_api_v1_workspaces__workspace_id__icon_get"];
    /** Put Workspace Icon */
    put: operations["put_workspace_icon_api_v1_workspaces__workspace_id__icon_put"];
    post?: never;
    /** Delete Workspace Icon */
    delete: operations["delete_workspace_icon_api_v1_workspaces__workspace_id__icon_delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/invitations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Invitations */
    get: operations["list_workspace_invitations_api_v1_workspaces__workspace_id__invitations_get"];
    put?: never;
    /** Create Workspace Invitation */
    post: operations["create_workspace_invitation_api_v1_workspaces__workspace_id__invitations_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/resend": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Resend Workspace Invitation */
    post: operations["resend_workspace_invitation_api_v1_workspaces__workspace_id__invitations__invitation_id__resend_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/invitations/{invitation_id}/revoke": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Revoke Workspace Invitation */
    post: operations["revoke_workspace_invitation_api_v1_workspaces__workspace_id__invitations__invitation_id__revoke_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/keys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Workspace Keys */
    get: operations["list_workspace_keys_api_v1_workspaces__workspace_id__keys_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/keys/{key_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    post?: never;
    /** Revoke Workspace Key */
    delete: operations["revoke_workspace_key_api_v1_workspaces__workspace_id__keys__key_id__delete"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/service-accounts": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Service Accounts */
    get: operations["list_service_accounts_api_v1_workspaces__workspace_id__service_accounts_get"];
    put?: never;
    /** Create Service Account */
    post: operations["create_service_account_api_v1_workspaces__workspace_id__service_accounts_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Service Account */
    get: operations["get_service_account_api_v1_workspaces__workspace_id__service_accounts__account_id__get"];
    put?: never;
    post?: never;
    /** Delete Service Account */
    delete: operations["delete_service_account_api_v1_workspaces__workspace_id__service_accounts__account_id__delete"];
    options?: never;
    head?: never;
    /** Update Service Account */
    patch: operations["update_service_account_api_v1_workspaces__workspace_id__service_accounts__account_id__patch"];
    trace?: never;
  };
  "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}/keys": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Service Account Keys */
    get: operations["list_service_account_keys_api_v1_workspaces__workspace_id__service_accounts__account_id__keys_get"];
    put?: never;
    /**
     * Create Service Account Key
     * @description Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation.
     */
    post: operations["create_service_account_key_api_v1_workspaces__workspace_id__service_accounts__account_id__keys_post"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/healthz": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Health */
    get: operations["health_healthz_get"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/readyz": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Ready
     * @description Ready while started and the database holds a usable schema. Redis only speeds work up, so losing it
     *     is reported as degraded rather than taking the replica out of service.
     */
    get: operations["ready_readyz_get"];
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
    /** AccountDisable */
    AccountDisable: {
      /**
       * Current Password
       * Format: password
       */
      current_password: string;
    };
    /** Agent */
    Agent: {
      /** Archived At */
      archived_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Default Revision Id */
      default_revision_id: string | null;
      /** Description */
      description: string;
      /** Id */
      id: string;
      /** Image Url */
      image_url: string | null;
      /** Labels */
      labels: {
        [key: string]: string;
      };
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
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** AgentConfig */
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
      connection_tools?: components["schemas"]["ConnectionSelection"][];
      /** Default Environment Template Id */
      default_environment_template_id?: string | null;
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      media_understanding?: components["schemas"]["MediaUnderstandingSelection"];
      /**
       * Memory Mounts
       * @default []
       */
      memory_mounts?: components["schemas"]["MemoryMount"][];
      /** Model */
      model: string;
      model_characteristics?: components["schemas"]["AgentModelCharacteristics"];
      /** Model Settings */
      model_settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /**
       * Plugins
       * @default []
       */
      plugins?: components["schemas"]["PluginSelection"][];
      retries?: components["schemas"]["RetryConfig"] | null;
      reviewer?: components["schemas"]["AgentReviewer"] | null;
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
      /**
       * User Questions
       * @default false
       */
      user_questions?: boolean;
    };
    /** AgentConfig */
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
      connection_tools?: components["schemas"]["ConnectionSelection"][];
      /** Default Environment Template Id */
      default_environment_template_id?: string | null;
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      media_understanding?: components["schemas"]["MediaUnderstandingSelection"];
      /**
       * Memory Mounts
       * @default []
       */
      memory_mounts?: components["schemas"]["MemoryMount"][];
      /** Model */
      model: string;
      model_characteristics?: components["schemas"]["AgentModelCharacteristics"];
      /** Model Settings */
      model_settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      output_spec?: components["schemas"]["OutputSpec"] | null;
      /**
       * Plugins
       * @default []
       */
      plugins?: components["schemas"]["PluginSelection"][];
      retries?: components["schemas"]["RetryConfig"] | null;
      reviewer?: components["schemas"]["AgentReviewer"] | null;
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
      /**
       * User Questions
       * @default false
       */
      user_questions?: boolean;
    };
    /** AgentCreate */
    AgentCreate: {
      config: components["schemas"]["AgentConfig-Input"];
      /**
       * Description
       * @default
       */
      description?: string;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
    };
    /**
     * AgentDuplicate
     * @description A new head whose first revision copies `revision_id`, by default the source's default revision.
     */
    AgentDuplicate: {
      /**
       * Description
       * @default
       */
      description?: string;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Revision Id */
      revision_id?: string | null;
    };
    /**
     * AgentModelCharacteristics
     * @description The agent's context policy, layered over what the model declares.
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
     * AgentOverride
     * @description What one run changes of its revision's configuration; an omitted or null field keeps the revision's.
     *
     *     `toolsets` replaces whole toolsets; `retries` and each subagent edge replace the fields they set, and a null
     *     edge removes it; every other field replaces the revision's value.
     */
    "AgentOverride-Input": {
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connection Tools */
      connection_tools?: components["schemas"]["ConnectionSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      media_understanding?:
        components["schemas"]["MediaUnderstandingSelection"] | null;
      /** Model */
      model?: string | null;
      model_characteristics?:
        components["schemas"]["AgentModelCharacteristics"] | null;
      /** Model Settings */
      model_settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
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
    /**
     * AgentOverride
     * @description What one run changes of its revision's configuration; an omitted or null field keeps the revision's.
     *
     *     `toolsets` replaces whole toolsets; `retries` and each subagent edge replace the fields they set, and a null
     *     edge removes it; every other field replaces the revision's value.
     */
    "AgentOverride-Output": {
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connection Tools */
      connection_tools?: components["schemas"]["ConnectionSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      media_understanding?:
        components["schemas"]["MediaUnderstandingSelection"] | null;
      /** Model */
      model?: string | null;
      model_characteristics?:
        components["schemas"]["AgentModelCharacteristics"] | null;
      /** Model Settings */
      model_settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
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
    /** AgentPage */
    AgentPage: {
      /** Items */
      items: components["schemas"]["Agent"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * AgentReviewer
     * @description The model reviewing calls whose permission is `review`, selected by model key.
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
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Digest */
      digest: string;
      /** Id */
      id: string;
      /** Note */
      note: string | null;
      /** Number */
      number: number;
    };
    /** AgentRevisionCreate */
    AgentRevisionCreate: {
      config: components["schemas"]["AgentConfig-Input"];
      /**
       * Make Default
       * @default true
       */
      make_default?: boolean;
      /** Note */
      note?: string | null;
    };
    /** AgentRevisionPage */
    AgentRevisionPage: {
      /** Items */
      items: components["schemas"]["AgentRevision"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** @enum {string} */
    AgentSource: "custom" | "builtin";
    /** AgentUpdate */
    AgentUpdate: {
      /** Description */
      description?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      } | null;
      /** Name */
      name?: string | null;
    };
    /** AgentUsage */
    AgentUsage: {
      /** Agent Id */
      agent_id: string;
      /** Name */
      name: string;
      runs: components["schemas"]["RunMetrics"];
      usage: components["schemas"]["ModelMetrics"];
    };
    /** AgentUsagePage */
    AgentUsagePage: {
      /** Items */
      items: components["schemas"]["AgentUsage"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * AgentValidate
     * @description A configuration to check as creating a revision would, storing nothing.
     *
     *     `agent_id` names the agent it would become a revision of, whose inline subagents may not lead back to it;
     *     omit it for a new agent.
     */
    AgentValidate: {
      /** Agent Id */
      agent_id?: string | null;
      config: components["schemas"]["AgentConfig-Input"];
    };
    /** ApiKey */
    ApiKey: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Expires At */
      expires_at: string | null;
      /** Id */
      id: string;
      /** Last Used At */
      last_used_at: string | null;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      principal: components["schemas"]["PrincipalSummary"];
      /** Revoked At */
      revoked_at: string | null;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** ApiKeyPage */
    ApiKeyPage: {
      /** Items */
      items: components["schemas"]["ApiKey"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    ApprovalDecision:
      components["schemas"]["Approve"] | components["schemas"]["Deny"];
    /** Approve */
    Approve: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "approve";
    };
    /** Asset */
    Asset: {
      /** Content Type */
      content_type: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Digest */
      digest: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Retired At */
      retired_at: string | null;
      /** Size */
      size: number;
      /** Source */
      source: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
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
    /** AssetCreate */
    AssetCreate: {
      /** Name */
      name: string;
      /** Upload Id */
      upload_id: string;
    };
    /** AssetPage */
    AssetPage: {
      /** Items */
      items: components["schemas"]["Asset"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AssetPart */
    AssetPart: {
      /** Asset Id */
      asset_id: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "asset";
    };
    /** AttemptView */
    AttemptView: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      failure: components["schemas"]["Failure"] | null;
      /** Finished At */
      finished_at: string | null;
      /** Harness Run Id */
      harness_run_id: string | null;
      /** Id */
      id: string;
      /** Number */
      number: number;
      /** Replaces Attempt Id */
      replaces_attempt_id: string | null;
      /** Run Id */
      run_id: string;
      /**
       * Start Reason
       * @enum {string}
       */
      start_reason: "initial" | "recovery" | "handoff";
      /** Started At */
      started_at: string | null;
      /**
       * Status
       * @enum {string}
       */
      status:
        "leased" | "running" | "succeeded" | "yielded" | "failed" | "cancelled";
      /** Worker Build */
      worker_build: string;
      /** Yield Reason */
      yield_reason: string | null;
    };
    /** Attempts */
    Attempts: {
      /** Items */
      items: components["schemas"]["AttemptView"][];
    };
    /** AuditEvent */
    AuditEvent: {
      /** Action */
      action: string;
      actor: components["schemas"]["PrincipalSummary"] | null;
      /** Actor Id */
      actor_id: string | null;
      /** Details */
      details: {
        [key: string]: components["schemas"]["JsonValue"];
      };
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
      /** Target Id */
      target_id: string;
      /** Target Kind */
      target_kind: string;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /** AuditPage */
    AuditPage: {
      /** Items */
      items: components["schemas"]["AuditEvent"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AuthConfiguration */
    AuthConfiguration: {
      /** Email Delivery */
      email_delivery: boolean;
      /** Initialized */
      initialized: boolean;
    };
    /** Authentication */
    Authentication: {
      /**
       * Cases
       * @default []
       */
      cases?: components["schemas"]["AuthenticationCase"][];
      /** @default required */
      mode?: components["schemas"]["CredentialMode"];
    };
    /**
     * AuthenticationCase
     * @description Override credential presence for one declared configuration field value.
     */
    AuthenticationCase: {
      /** Equals */
      equals: string | number | boolean | null;
      /** Field */
      field: string;
      mode: components["schemas"]["CredentialMode"];
    };
    /** AuthorizationRequest */
    AuthorizationRequest: {
      /** Return Url */
      return_url?: string | null;
    };
    /**
     * AuthorizationResult
     * @description A browser flow to follow, or none when the grant obtained the credential directly.
     */
    AuthorizationResult: {
      /** Expires At */
      expires_at: string | null;
      /** Redirect Url */
      redirect_url: string | null;
    };
    /** BearerCredential */
    BearerCredential: {
      /**
       * Token
       * Format: password
       */
      token: string;
    };
    /** BootstrapInput */
    BootstrapInput: {
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
    CallResult:
      components["schemas"]["Returned"] | components["schemas"]["Failed"];
    /** CallbackOutcome */
    CallbackOutcome: {
      /** Connection Id */
      connection_id: string;
      /** Error */
      error: string | null;
      status: components["schemas"]["ConnectionStatus"];
    };
    /**
     * CatalogModel
     * @description A catalog model, with the characteristics and pricing a model created from it starts with.
     */
    CatalogModel: {
      characteristics: components["schemas"]["HarnessModelCharacteristics-Output"];
      /** Identity */
      identity: string;
      /** Name */
      name: string;
      pricing: components["schemas"]["ModelPricingEntry-Output"] | null;
      /** Pricing Warning */
      pricing_warning: string | null;
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
     * @description A models.dev channel and the model ID it lists there.
     */
    CatalogRef: {
      /** Model */
      model: string;
      /** Provider */
      provider: string;
    };
    /** @enum {string} */
    Certainty: "not_dispatched" | "known" | "unknown";
    /**
     * ChildEnvironmentPolicy
     * @description What a child run mounts: no environment, the parent's, or a new one from `template_id`.
     */
    ChildEnvironmentPolicy: {
      /**
       * Mode
       * @default shared
       * @enum {string}
       */
      mode?: "none" | "shared" | "dedicated";
      /** Template Id */
      template_id?: string | null;
    };
    /** @enum {string} */
    ClientAuthentication: "none" | "client_secret_post" | "client_secret_basic";
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
    /** Connection */
    Connection: {
      auth: components["schemas"]["ConnectionAuth"];
      /** Authorization Pending */
      authorization_pending: boolean;
      /** Client Secret Configured */
      client_secret_configured: boolean;
      config: components["schemas"]["ConnectionConfig"];
      /** Connector Provider Id */
      connector_provider_id: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      failure: components["schemas"]["ConnectionFailure"] | null;
      /** Id */
      id: string;
      last_test: components["schemas"]["ConnectionTestOutcome"] | null;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      status: components["schemas"]["ConnectionStatus"];
      /** Type */
      type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** @enum {string} */
    ConnectionAuth: "none" | "bearer" | "headers" | "oauth" | "account";
    ConnectionConfig:
      | components["schemas"]["McpConfig"]
      | components["schemas"]["ConnectorConfig"];
    /** ConnectionCreate */
    ConnectionCreate: {
      /** @default none */
      auth?: components["schemas"]["ConnectionAuth"];
      /** Client Secret */
      client_secret?: string | null;
      config: components["schemas"]["ConnectionConfig"];
      /** Connector Provider Id */
      connector_provider_id?: string | null;
      credential?: components["schemas"]["EnteredCredential"] | null;
      /** Name */
      name: string;
      /** Type */
      type: string;
    };
    /**
     * ConnectionFailure
     * @description Why the last remote operation failed; `outcome_unknown` means it may have taken effect.
     */
    ConnectionFailure: {
      /** Code */
      code: string | null;
      /** Operation Id */
      operation_id: string;
      operation_kind: components["schemas"]["OperationKind"];
      /**
       * Reason
       * @enum {string}
       */
      reason: "rejected" | "outcome_unknown";
    };
    /** ConnectionPage */
    ConnectionPage: {
      /** Items */
      items: components["schemas"]["Connection"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ConnectionSelection
     * @description An agent's use of one connection: all the tools it exposes, or a subset, and their permissions.
     */
    ConnectionSelection: {
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
    /** @enum {string} */
    ConnectionStatus: "pending" | "ready" | "reauthorization_required";
    /** ConnectionTest */
    ConnectionTest: {
      /** Connection Id */
      connection_id: string;
      /** Connection Version */
      connection_version: number;
      /** Message */
      message: string | null;
      /**
       * Status
       * @enum {string}
       */
      status: "succeeded" | "failed";
      /**
       * Tested At
       * Format: date-time
       */
      tested_at: string;
      /** Tools */
      tools: components["schemas"]["ToolInfo"][];
    };
    /**
     * ConnectionTestOutcome
     * @description What a test found for the connection version it tested.
     */
    ConnectionTestOutcome: {
      /** Connection Version */
      connection_version: number;
      /** Message */
      message: string | null;
      /**
       * Status
       * @enum {string}
       */
      status: "succeeded" | "failed";
      /**
       * Tested At
       * Format: date-time
       */
      tested_at: string;
    };
    /** ConnectionUpdate */
    ConnectionUpdate: {
      auth?: components["schemas"]["ConnectionAuth"] | null;
      /** Client Secret */
      client_secret?: string | null;
      config?: components["schemas"]["ConnectionConfig"] | null;
      credential?: components["schemas"]["EnteredCredential"] | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Name */
      name?: string | null;
    };
    /** ConnectorActionPage */
    ConnectorActionPage: {
      /** Items */
      items: components["schemas"]["ToolInfo"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ConnectorApp */
    ConnectorApp: {
      /** Authentication Methods */
      authentication_methods: string[];
      /** Description */
      description: string | null;
      /** Key */
      key: string;
      /** Logo Url */
      logo_url: string | null;
      /** Name */
      name: string;
      /** Setup Schema */
      setup_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Unavailable Reason */
      unavailable_reason: string | null;
    };
    /** ConnectorAppPage */
    ConnectorAppPage: {
      /** Items */
      items: components["schemas"]["ConnectorApp"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ConnectorConfig
     * @description One app of a connector provider and the actions it exposes; `setup` is validated by the provider type.
     */
    ConnectorConfig: {
      /** Actions */
      actions: string[];
      /** App */
      app: string;
      /** Setup */
      setup?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /** CreatedSubscription */
    CreatedSubscription: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Enabled */
      enabled: boolean;
      filter: components["schemas"]["SubscriptionFilter"];
      /** Id */
      id: string;
      /** Kinds */
      kinds: string[];
      /** Name */
      name: string;
      /** Signing Secret */
      signing_secret: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Id */
      updated_by_id: string;
      /** Url */
      url: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /**
     * CredentialMode
     * @enum {string}
     */
    CredentialMode: "required" | "optional" | "forbidden";
    /** DailyUsage */
    DailyUsage: {
      /**
       * Date
       * Format: date
       */
      date: string;
      usage: components["schemas"]["ModelMetrics"];
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
    /** @enum {string} */
    Delivery: "steer" | "next_run";
    /** DeliveryPage */
    DeliveryPage: {
      /** Items */
      items: components["schemas"]["WebhookDelivery"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Deny */
    Deny: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      action: "deny";
      /** Reason */
      reason?: string | null;
    };
    /** EmailChangeConfirm */
    EmailChangeConfirm: {
      /** Token */
      token: string;
    };
    EnteredCredential:
      | components["schemas"]["BearerCredential"]
      | components["schemas"]["HeadersCredential"];
    /** EntryPage */
    EntryPage: {
      /** Items */
      items: components["schemas"]["EntryView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** @enum {string} */
    EntryStatus: "pending" | "assigned" | "consumed" | "failed" | "withdrawn";
    /**
     * EntryUpdate
     * @description Pending entries only; the original request digest never changes.
     */
    EntryUpdate: {
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      delivery?: components["schemas"]["Delivery"] | null;
      options?: components["schemas"]["RunOptions-Input"] | null;
      payload?: components["schemas"]["MessagePayload"] | null;
    };
    /** EntryView */
    EntryView: {
      /** Agent Id */
      agent_id: string | null;
      /** Agent Revision Id */
      agent_revision_id: string | null;
      /** Assigned Run Id */
      assigned_run_id: string | null;
      /** Child Run Id */
      child_run_id: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      delivery: components["schemas"]["Delivery"];
      failure: components["schemas"]["Failure"] | null;
      /** Finished At */
      finished_at: string | null;
      /** Id */
      id: string;
      /** Incorporated Checkpoint Seq */
      incorporated_checkpoint_seq: number | null;
      /**
       * Kind
       * @enum {string}
       */
      kind: "message" | "child_result";
      options: components["schemas"]["RunOptions-Output"];
      /** Origin Run Id */
      origin_run_id: string | null;
      /** Payload */
      payload: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Position */
      position: number;
      /** Principal Id */
      principal_id: string;
      status: components["schemas"]["EntryStatus"];
      /** Thread Id */
      thread_id: string;
    };
    /**
     * EnvironmentFailure
     * @description The last error of the outstanding operation or, on a ready instance, of its last renewal.
     *
     *     `unknown` means the call may have taken effect: only the same operation may continue. `permanent` failures
     *     refuse new mounts and acceptance until the cause is fixed or the instance is deleted; `environment_lost` means
     *     the provider no longer has the sandbox.
     */
    EnvironmentFailure: {
      /**
       * At
       * Format: date-time
       */
      at: string;
      certainty: components["schemas"]["Certainty"];
      /** Code */
      code: string;
      /** Message */
      message: string;
      /** Operation Id */
      operation_id: string | null;
      /** Permanent */
      permanent: boolean;
    };
    /** EnvironmentMount */
    EnvironmentMount: {
      /** Environment Id */
      environment_id: string;
      /** Name */
      name: string;
      /** Working Directory */
      working_directory?: string | null;
    };
    /** EnvironmentPage */
    EnvironmentPage: {
      /** Items */
      items: components["schemas"]["EnvironmentView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * EnvironmentUpdate
     * @description Fields left out stay unchanged. Only an external target has an endpoint and token; a new endpoint comes with
     *     its token, so a stored token never reaches an endpoint it was not entered for.
     */
    EnvironmentUpdate: {
      /** Endpoint */
      endpoint?: string | null;
      /** Name */
      name?: string | null;
      /** Token */
      token?: string | null;
    };
    /** EnvironmentView */
    EnvironmentView: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Device Id */
      device_id: string | null;
      /** Endpoint */
      endpoint: string | null;
      failure: components["schemas"]["EnvironmentFailure"] | null;
      /** Id */
      id: string;
      /** Last Used At */
      last_used_at: string | null;
      /** Name */
      name: string;
      /** Operation Id */
      operation_id: string | null;
      /** Operation Started At */
      operation_started_at: string | null;
      /** Organization Id */
      organization_id: string;
      /** Owner Principal Id */
      owner_principal_id: string | null;
      /** Provider Id */
      provider_id: string | null;
      /** Status */
      status: string;
      /** Template Id */
      template_id: string | null;
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
    /** ErrorBody */
    ErrorBody: {
      code: components["schemas"]["ErrorCode"];
      /** Details */
      details: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Message */
      message: string;
      /** Request Id */
      request_id: string | null;
    };
    /** @enum {string} */
    ErrorCode:
      | "invalid_argument"
      | "invalid_cursor"
      | "unauthenticated"
      | "forbidden"
      | "not_found"
      | "already_exists"
      | "conflict"
      | "precondition_failed"
      | "precondition_required"
      | "payload_too_large"
      | "request_timeout"
      | "disabled"
      | "unavailable"
      | "rate_limited"
      | "internal";
    /**
     * ErrorEnvelope
     * @description How every failure answers, whatever its status.
     */
    ErrorEnvelope: {
      error: components["schemas"]["ErrorBody"];
    };
    /**
     * ExternalTargetCreate
     * @description An envd daemon someone runs, registered by its endpoint and the token it accepts.
     */
    ExternalTargetCreate: {
      /** Endpoint */
      endpoint: string;
      /** Name */
      name?: string | null;
      /**
       * Token
       * Format: password
       */
      token: string;
    };
    /**
     * Failed
     * @description An explicit external tool failure, including an intentional unanswered question.
     */
    Failed: {
      /** Message */
      message: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      status: "failed";
    };
    /** Failure */
    Failure: {
      /** Code */
      code: string;
      /** Message */
      message: string;
    };
    /** Fork */
    Fork: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      /** @default steer */
      delivery?: components["schemas"]["Delivery"];
      /** @default [] */
      environments?: components["schemas"]["InitialMounts"];
      /**
       * Fresh Environments
       * @default false
       */
      fresh_environments?: boolean;
      /**
       * Kind
       * @default message
       * @constant
       */
      kind?: "message";
      /**
       * Memories
       * @default []
       */
      memories?: components["schemas"]["MemoryMount"][];
      options?: components["schemas"]["RunOptions-Input"];
      payload: components["schemas"]["MessagePayload"];
    };
    /**
     * GitHubSource
     * @description A directory of a public GitHub repository.
     *
     *     `ref` defaults to the default branch. On a request `commit` is an optional expectation the resolved ref
     *     must meet; in a manifest it records the commit the package was read from.
     */
    GitHubSource: {
      /** Commit */
      commit?: string | null;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "github";
      /**
       * Path
       * @default
       */
      path?: string;
      /** Ref */
      ref?: string | null;
      /** Repository */
      repository: string;
    };
    /** GrantCreate */
    GrantCreate: {
      /** Principal Id */
      principal_id: string;
      /** Role */
      role: string;
    };
    /** GrantPage */
    GrantPage: {
      /** Items */
      items: components["schemas"]["GrantView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** GrantUpdate */
    GrantUpdate: {
      /** Role */
      role: string;
    };
    /** GrantView */
    GrantView: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Id */
      id: string;
      /** Organization Id */
      organization_id: string;
      principal: components["schemas"]["PrincipalSummary"];
      /** Role */
      role: string;
      /** Workspace Id */
      workspace_id: string | null;
    };
    /**
     * HarnessModelCharacteristics
     * @description Resolved Harness characteristics of the active Agent model.
     */
    "HarnessModelCharacteristics-Input": {
      /** Capabilities */
      capabilities?: components["schemas"]["ModelCapability"][];
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
     * HarnessModelCharacteristics
     * @description Resolved Harness characteristics of the active Agent model.
     */
    "HarnessModelCharacteristics-Output": {
      /** Capabilities */
      capabilities?: string[];
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
    /** HeadersCredential */
    HeadersCredential: {
      /** Headers */
      headers: {
        [key: string]: string;
      };
    };
    /** HistoryPurge */
    HistoryPurge: {
      /** Purged */
      purged: number;
    };
    /** InboxOrder */
    InboxOrder: {
      /** Entry Ids */
      entry_ids: string[];
    };
    InitialMounts: components["schemas"]["MountCreate"][];
    /**
     * InstrumentationScope
     * @description The instrumentation library that recorded the span.
     */
    InstrumentationScope: {
      /** Name */
      name: string;
      /** Version */
      version: string | null;
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
      /** Email */
      email: string;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Id */
      id: string;
      /** Invited By Id */
      invited_by_id: string;
      /** Organization Id */
      organization_id: string;
      /** Principal Id */
      principal_id: string | null;
      /** Revoked At */
      revoked_at: string | null;
      /** Role */
      role: string;
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
    /** InvitationAccept */
    InvitationAccept: {
      /** Name */
      name?: string | null;
      /**
       * Password
       * Format: password
       */
      password: string;
      /** Token */
      token: string;
    };
    /** InvitationCreate */
    InvitationCreate: {
      /**
       * Email
       * Format: email
       */
      email: string;
      /** Role */
      role: string;
    };
    /** InvitationPage */
    InvitationPage: {
      /** Items */
      items: components["schemas"]["Invitation"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** InvitationReceipt */
    InvitationReceipt: {
      /**
       * Delivery
       * @enum {string}
       */
      delivery: "queued" | "manual";
      invitation: components["schemas"]["Invitation"];
      /** Invitation Url */
      invitation_url: string | null;
    };
    /** IssuedKey */
    IssuedKey: {
      key: components["schemas"]["ApiKey"];
      /** Secret */
      secret: string;
    };
    /** Item */
    Item: {
      /** Content */
      content: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Ended At */
      ended_at?: string | null;
      /** First Stream Id */
      first_stream_id: string;
      /** Id */
      id: string;
      kind: components["schemas"]["ItemKind"];
      /** Last Stream Id */
      last_stream_id: string;
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
      state: components["schemas"]["ItemState"];
    };
    /** @enum {string} */
    ItemKind:
      "text_message" | "reasoning_message" | "tool_call" | "observation";
    /** @enum {string} */
    ItemState: "in_progress" | "completed" | "interrupted" | "failed";
    /** JsonPart */
    JsonPart: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "json";
      value: components["schemas"]["JsonValue"];
    };
    JsonValue: unknown;
    /** KeyCreate */
    KeyCreate: {
      /** Expires At */
      expires_at?: string | null;
      /** Name */
      name: string;
    };
    /** @enum {string} */
    LifecycleKind:
      | "run.accepted"
      | "run.running"
      | "run.waiting"
      | "run.completed"
      | "run.failed"
      | "run.cancelled"
      | "run_attempt.leased"
      | "run_attempt.running"
      | "run_attempt.succeeded"
      | "run_attempt.yielded"
      | "run_attempt.failed"
      | "run_attempt.cancelled";
    /** @enum {string} */
    Lineage: "root" | "continue" | "fork";
    /** LoginInput */
    LoginInput: {
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
    /** LoginOutput */
    LoginOutput: {
      /** Csrf Token */
      csrf_token: string;
      /** Principal Id */
      principal_id: string;
    };
    /** LoginSession */
    LoginSession: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current */
      current: boolean;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /** Id */
      id: string;
    };
    /** LoginSessionPage */
    LoginSessionPage: {
      /** Items */
      items: components["schemas"]["LoginSession"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ManagedEnvironmentCreate
     * @description A workspace-managed sandbox reserved from a template, for threads to mount; maintenance creates it.
     */
    ManagedEnvironmentCreate: {
      /** Name */
      name?: string | null;
      /** Template Id */
      template_id: string;
    };
    /** @enum {string} */
    McpAuth: "none" | "bearer" | "headers" | "oauth";
    /** McpConfig */
    McpConfig: {
      /**
       * Headers
       * @default []
       */
      headers?: string[];
      oauth?: components["schemas"]["OAuthSettings"] | null;
      /** Tools */
      tools?: string[] | null;
      /** Url */
      url: string;
    };
    McpHeaders: {
      [key: string]: {
        [key: string]: string;
      };
    };
    /** McpServer */
    McpServer: {
      auth: components["schemas"]["McpAuth"];
      /** Description */
      description: string;
      /** Documentation Url */
      documentation_url?: string | null;
      /**
       * Header Names
       * @default []
       */
      header_names?: string[];
      /** Key */
      key: string;
      /** Logo Url */
      logo_url?: string | null;
      /** Name */
      name: string;
      /**
       * Requirements
       * @default
       */
      requirements?: string;
      /** Url */
      url: string;
    };
    /** McpServerPage */
    McpServerPage: {
      /** Items */
      items: components["schemas"]["McpServer"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * MediaDefaults
     * @description A workspace's media-understanding models for agents that select none for a kind; `id` is the workspace's.
     */
    MediaDefaults: {
      /** Audio */
      audio?: string | null;
      /** Id */
      id: string;
      /** Image */
      image?: string | null;
      /** Version */
      version: number;
      /** Video */
      video?: string | null;
    };
    /**
     * MediaUnderstandingSelection
     * @description The model, by key, describing each media kind a model cannot read; a kind without one is unavailable.
     */
    MediaUnderstandingSelection: {
      /** Audio */
      audio?: string | null;
      /** Image */
      image?: string | null;
      /** Video */
      video?: string | null;
    };
    /** MemberPage */
    MemberPage: {
      /** Items */
      items: components["schemas"]["PrincipalSummary"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** Memory */
    Memory: {
      /** Always Load */
      always_load: string[];
      /** Content Bytes */
      content_bytes: number | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Description */
      description: string | null;
      /** File Count */
      file_count: number | null;
      /** Guide */
      guide: string | null;
      /** History Bytes */
      history_bytes: number | null;
      /** Id */
      id: string;
      /** Inherited Guide */
      inherited_guide: string;
      kind: components["schemas"]["MemoryKind"];
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Namespace */
      namespace: string | null;
      /** Organization Id */
      organization_id: string;
      /** Provider Id */
      provider_id: string | null;
      /** Type */
      type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** @enum {string} */
    MemoryAccess: "read" | "write";
    /**
     * MemoryCreate
     * @description `postgres` makes a file memory the Service stores; a Memory Provider's type makes a record memory in that
     *     provider's backend, under a new namespace or the existing one `namespace` adopts.
     */
    MemoryCreate: {
      /**
       * Always Load
       * @default []
       */
      always_load?: string[];
      /** Description */
      description?: string | null;
      /** Guide */
      guide?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Namespace */
      namespace?: string | null;
      /** Provider Id */
      provider_id?: string | null;
      /**
       * Type
       * @default postgres
       */
      type?: string;
    };
    /** MemoryFile */
    MemoryFile: {
      /** Content */
      content: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Description */
      description: string | null;
      /** Id */
      id: string;
      /** Path */
      path: string;
      /** Size */
      size: number;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Principal Id */
      updated_by_principal_id: string | null;
      /** Updated By Run Id */
      updated_by_run_id: string | null;
      /** Version */
      version: number;
    };
    /** MemoryFileCreate */
    MemoryFileCreate: {
      /** Content */
      content: string;
      /** Path */
      path: string;
    };
    /** MemoryFileEntry */
    MemoryFileEntry: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Description */
      description: string | null;
      /** Id */
      id: string;
      /** Path */
      path: string;
      /** Size */
      size: number;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Principal Id */
      updated_by_principal_id: string | null;
      /** Updated By Run Id */
      updated_by_run_id: string | null;
      /** Version */
      version: number;
    };
    /**
     * MemoryFileMove
     * @description Moves the file `If-Match` names to a free path; it keeps its ID.
     */
    MemoryFileMove: {
      /** Destination */
      destination: string;
      /** Source */
      source: string;
    };
    /** MemoryFilePage */
    MemoryFilePage: {
      /** Items */
      items: components["schemas"]["MemoryFileEntry"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** MemoryFileReplace */
    MemoryFileReplace: {
      /** Content */
      content: string;
    };
    /**
     * MemoryFileState
     * @description A path after a restore: its file, or null when the restored state is no file.
     */
    MemoryFileState: {
      file: components["schemas"]["MemoryFile"] | null;
      /** Path */
      path: string;
    };
    /** @enum {string} */
    MemoryKind: "file" | "record";
    /**
     * MemoryMount
     * @description A memory under the name the model addresses it by, exposing the tools its access allows. `recall` lets a
     *     record memory recall records into each run's first input; file memories ignore it.
     */
    MemoryMount: {
      access: components["schemas"]["MemoryAccess"];
      /** Memory Id */
      memory_id: string;
      /** Name */
      name: string;
      /**
       * Recall
       * @default true
       */
      recall?: boolean;
    };
    /** MemoryMountPage */
    MemoryMountPage: {
      /** Items */
      items: components["schemas"]["MemoryMount"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /**
     * MemoryMountUpdate
     * @description Fields left out stay unchanged.
     */
    MemoryMountUpdate: {
      access?: components["schemas"]["MemoryAccess"] | null;
      /** Recall */
      recall?: boolean | null;
    };
    /** MemoryPage */
    MemoryPage: {
      /** Items */
      items: components["schemas"]["Memory"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** MemoryRecordPage */
    MemoryRecordPage: {
      /** Items */
      items: components["schemas"]["MemoryRecordView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** MemoryRecordSearch */
    MemoryRecordSearch: {
      /**
       * Limit
       * @default 10
       */
      limit?: number;
      /** Query */
      query: string;
    };
    /** MemoryRecordText */
    MemoryRecordText: {
      /** Text */
      text: string;
    };
    /**
     * MemoryRecordView
     * @description A record as the memory's provider returns it; `score` is its similarity to a search query.
     */
    MemoryRecordView: {
      /** Id */
      id: string;
      /** Score */
      score?: number | null;
      /** Text */
      text: string;
      /** Updated At */
      updated_at?: string | null;
    };
    /**
     * MemoryRevision
     * @description One change to one path. A move is two: `move_out` at the source and `move_in` at the destination.
     */
    MemoryRevision: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Moved Path */
      moved_path: string | null;
      /**
       * Op
       * @enum {string}
       */
      op: "create" | "update" | "delete" | "move_out" | "move_in";
      /** Path */
      path: string;
      /** Principal Id */
      principal_id: string | null;
      /** Run Id */
      run_id: string | null;
      /** Seq */
      seq: number;
      /** Tool Call Id */
      tool_call_id: string | null;
    };
    /** MemoryRevisionDetail */
    MemoryRevisionDetail: {
      /** Content */
      content: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Hunks */
      hunks: string[];
      /** Moved Path */
      moved_path: string | null;
      /**
       * Op
       * @enum {string}
       */
      op: "create" | "update" | "delete" | "move_out" | "move_in";
      /** Path */
      path: string;
      /** Previous Content */
      previous_content: string | null;
      /** Principal Id */
      principal_id: string | null;
      /** Run Id */
      run_id: string | null;
      /** Seq */
      seq: number;
      /** Tool Call Id */
      tool_call_id: string | null;
    };
    /** MemoryRevisionPage */
    MemoryRevisionPage: {
      /** Items */
      items: components["schemas"]["MemoryRevision"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * MemoryUpdate
     * @description Fields left out stay unchanged; `description: null` clears it and `guide: null` inherits the default.
     */
    MemoryUpdate: {
      /** Always Load */
      always_load?: string[] | null;
      /** Description */
      description?: string | null;
      /** Guide */
      guide?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      } | null;
      /** Name */
      name?: string | null;
    };
    /** Message */
    Message: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      /** @default steer */
      delivery?: components["schemas"]["Delivery"];
      /**
       * Kind
       * @default message
       * @constant
       */
      kind?: "message";
      options?: components["schemas"]["RunOptions-Input"];
      payload: components["schemas"]["MessagePayload"];
    };
    /** @description Pydantic AI ModelMessage JSON objects, validated by the Service. Imports completed user text, model text and closed tool-call/JSON-result exchanges; no instructions, media or suspended execution. At most 256 messages and 256 KiB of normalized JSON. */
    MessageHistory: {
      [key: string]: components["schemas"]["JsonValue"];
    }[];
    /** MessagePayload */
    MessagePayload: {
      /** Content */
      content: components["schemas"]["Part"][];
    };
    /** Model */
    Model: {
      catalog_ref: components["schemas"]["CatalogRef"] | null;
      config: components["schemas"]["ModelConfig-Output"];
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Description */
      description: string;
      /** Enabled */
      enabled: boolean;
      /** Key */
      key: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      pricing: components["schemas"]["ModelPricingEntry-Output"] | null;
      /** Provider Id */
      provider_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /**
     * ModelCapability
     * @description Harness-owned capabilities of the active Agent model.
     * @enum {string}
     */
    ModelCapability:
      | "image_understanding"
      | "video_understanding"
      | "audio_understanding"
      | "document_understanding";
    /**
     * ModelCatalog
     * @description `ready` is fresh, `stale` the last catalog after a failed refresh, `unavailable` none fetched yet.
     */
    ModelCatalog: {
      /** Items */
      items: components["schemas"]["CatalogModel"][];
      /**
       * Status
       * @enum {string}
       */
      status: "ready" | "stale" | "unavailable";
    };
    /** ModelConfig */
    "ModelConfig-Input": {
      characteristics?: components["schemas"]["HarnessModelCharacteristics-Input"];
      /** Extra Body */
      extra_body?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Extra Headers */
      extra_headers?: {
        [key: string]: string;
      };
      /** Max Tokens */
      max_tokens?: number | null;
      /** Model Api */
      model_api: string;
      /** Model Name */
      model_name: string;
      /** Temperature */
      temperature?: number | null;
      /** Top P */
      top_p?: number | null;
    };
    /** ModelConfig */
    "ModelConfig-Output": {
      characteristics?: components["schemas"]["HarnessModelCharacteristics-Output"];
      /** Extra Body */
      extra_body?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Extra Headers */
      extra_headers?: {
        [key: string]: string;
      };
      /** Max Tokens */
      max_tokens?: number | null;
      /** Model Api */
      model_api: string;
      /** Model Name */
      model_name: string;
      /** Temperature */
      temperature?: number | null;
      /** Top P */
      top_p?: number | null;
    };
    /** ModelCreate */
    ModelCreate: {
      catalog_ref?: components["schemas"]["CatalogRef"] | null;
      config: components["schemas"]["ModelConfig-Input"];
      /**
       * Description
       * @default
       */
      description?: string;
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Key */
      key?: string | null;
      /** Name */
      name: string;
      pricing?: components["schemas"]["ModelPricingEntry-Input"] | null;
      /** Provider Id */
      provider_id: string;
    };
    /** ModelMetrics */
    ModelMetrics: {
      /** Cache Hit Rate */
      cache_hit_rate: number | null;
      /** Cache Read Tokens */
      cache_read_tokens: number;
      /** Cost */
      cost: string | null;
      /** Input Tokens */
      input_tokens: number;
      /** Output Tokens */
      output_tokens: number;
      /** Requests */
      requests: number;
      /** Unpriced Requests */
      unpriced_requests: number;
    };
    /** ModelPage */
    ModelPage: {
      /** Items */
      items: components["schemas"]["Model"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ModelPriceRule
     * @description One complete set of prices and the condition selecting it.
     */
    "ModelPriceRule-Input": {
      constraint?: components["schemas"]["PricingConstraint"];
      /** Max Input Tokens */
      max_input_tokens?: number | null;
      /** Prices */
      prices: components["schemas"]["PriceComponent-Input"][];
      /** Rule Id */
      rule_id: string;
      /** Service Tier */
      service_tier?: string | null;
    };
    /**
     * ModelPriceRule
     * @description One complete set of prices and the condition selecting it.
     */
    "ModelPriceRule-Output": {
      constraint?: components["schemas"]["PricingConstraint"];
      /** Max Input Tokens */
      max_input_tokens?: number | null;
      /** Prices */
      prices: components["schemas"]["PriceComponent-Output"][];
      /** Rule Id */
      rule_id: string;
      /** Service Tier */
      service_tier?: string | null;
    };
    /**
     * ModelPricingEntry
     * @description Complete pricing declaration for one provider-qualified model.
     */
    "ModelPricingEntry-Input": {
      /** Context Window */
      context_window?: number | null;
      /** Model */
      model: string;
      /** Provider */
      provider: string;
      /** Rules */
      rules: components["schemas"]["ModelPriceRule-Input"][];
      /** Source */
      source: string;
      /** Source Revision */
      source_revision: string;
      /** Source Url */
      source_url?: string | null;
    };
    /**
     * ModelPricingEntry
     * @description Complete pricing declaration for one provider-qualified model.
     */
    "ModelPricingEntry-Output": {
      /** Context Window */
      context_window?: number | null;
      /** Model */
      model: string;
      /** Provider */
      provider: string;
      /** Rules */
      rules: components["schemas"]["ModelPriceRule-Output"][];
      /** Source */
      source: string;
      /** Source Revision */
      source_revision: string;
      /** Source Url */
      source_url?: string | null;
    };
    /** ModelUpdate */
    ModelUpdate: {
      catalog_ref?: components["schemas"]["CatalogRef"] | null;
      config?: components["schemas"]["ModelConfig-Input"] | null;
      /** Description */
      description?: string | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Name */
      name?: string | null;
      pricing?: components["schemas"]["ModelPricingEntry-Input"] | null;
    };
    /** ModelUsage */
    ModelUsage: {
      /** Cache Read Tokens */
      cache_read_tokens: number;
      /** Cache Write Tokens */
      cache_write_tokens: number;
      /** Cost */
      cost: string | null;
      /** Input Tokens */
      input_tokens: number;
      /** Model */
      model: string | null;
      /** Output Tokens */
      output_tokens: number;
      /** Requests */
      requests: number;
    };
    /** ModelUsageGroup */
    ModelUsageGroup: {
      /** Model */
      model: string | null;
      /** Name */
      name: string | null;
      usage: components["schemas"]["ModelMetrics"];
    };
    /** ModelUsagePage */
    ModelUsagePage: {
      /** Items */
      items: components["schemas"]["ModelUsageGroup"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** MountCreate */
    MountCreate: {
      /** Environment Id */
      environment_id: string;
      /** Name */
      name: string;
      /** Working Directory */
      working_directory?: string | null;
    };
    /** MountPage */
    MountPage: {
      /** Items */
      items: components["schemas"]["MountView"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** MountView */
    MountView: {
      /** Environment Id */
      environment_id: string;
      /** Name */
      name: string;
      /** Working Directory */
      working_directory: string | null;
    };
    /** NewThread */
    NewThread: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id?: string | null;
      /** @default steer */
      delivery?: components["schemas"]["Delivery"];
      /** @default [] */
      environments?: components["schemas"]["InitialMounts"];
      /**
       * Kind
       * @default message
       * @constant
       */
      kind?: "message";
      mcp_headers?: components["schemas"]["McpHeaders"];
      /**
       * Memories
       * @default []
       */
      memories?: components["schemas"]["MemoryMount"][];
      message_history?: components["schemas"]["MessageHistory"];
      options?: components["schemas"]["RunOptions-Input"];
      payload: components["schemas"]["MessagePayload"];
      /** Session Id */
      session_id?: string | null;
    };
    /** @enum {string} */
    OAuthGrant: "authorization_code" | "client_credentials";
    /** OAuthRedirect */
    OAuthRedirect: {
      /** Redirect Uri */
      redirect_uri: string;
    };
    /** OAuthSettings */
    OAuthSettings: {
      /** Client Id */
      client_id?: string | null;
      /** @default authorization_code */
      grant_type?: components["schemas"]["OAuthGrant"];
      /**
       * Scopes
       * @default []
       */
      scopes?: string[];
      /** @default none */
      token_endpoint_auth_method?: components["schemas"]["ClientAuthentication"];
    };
    /** @enum {string} */
    OperationKind: "setup" | "complete" | "refresh" | "revoke";
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
      image_url: string | null;
      /** Name */
      name: string;
      /** Permissions */
      permissions: components["schemas"]["Verb"][];
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /** OrganizationPage */
    OrganizationPage: {
      /** Items */
      items: components["schemas"]["Organization"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** OrganizationUpdate */
    OrganizationUpdate: {
      /** Name */
      name?: string | null;
    };
    /**
     * OutputSpec
     * @description Plain text without a schema, one structured output, or at least two named variants.
     *
     *     `resources` are the schemas `$ref`s may name besides the schema's own definitions.
     */
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
    Part:
      | components["schemas"]["TextPart"]
      | components["schemas"]["AssetPart"]
      | components["schemas"]["UrlPart"]
      | components["schemas"]["JsonPart"];
    /** PasswordChange */
    PasswordChange: {
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
    /** PasswordReset */
    PasswordReset: {
      /**
       * Email
       * Format: email
       */
      email: string;
    };
    /** PasswordResetConfirm */
    PasswordResetConfirm: {
      /**
       * Password
       * Format: password
       */
      password: string;
      /** Token */
      token: string;
    };
    /**
     * Pending
     * @description Public projection; complete native requests and private metadata stay in the checkpoint.
     */
    Pending: {
      /** Approvals */
      approvals: components["schemas"]["PendingCall"][];
      /** Calls */
      calls: components["schemas"]["PendingCall"][];
    };
    /** PendingCall */
    PendingCall: {
      /** Arguments */
      arguments: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Presentation */
      presentation?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      tool_call_id: components["schemas"]["ToolCallId"];
      /** Tool Name */
      tool_name: string;
    };
    /**
     * PluginSelection
     * @description One instance of a Harness plugin factory the deployment installed.
     */
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
    /**
     * PriceComponent
     * @description One genai-prices usage dimension and its USD unit price.
     */
    "PriceComponent-Input": {
      /** Price */
      price: number | string;
      /** Price Key */
      price_key: string;
      /**
       * Tiers
       * @default []
       */
      tiers?: components["schemas"]["PriceTier-Input"][];
    };
    /**
     * PriceComponent
     * @description One genai-prices usage dimension and its USD unit price.
     */
    "PriceComponent-Output": {
      /** Price */
      price: string;
      /** Price Key */
      price_key: string;
      /**
       * Tiers
       * @default []
       */
      tiers?: components["schemas"]["PriceTier-Output"][];
    };
    /**
     * PriceTier
     * @description One cliff-pricing threshold applied to the complete usage quantity.
     */
    "PriceTier-Input": {
      /** Price */
      price: number | string;
      /** Start */
      start: number;
    };
    /**
     * PriceTier
     * @description One cliff-pricing threshold applied to the complete usage quantity.
     */
    "PriceTier-Output": {
      /** Price */
      price: string;
      /** Start */
      start: number;
    };
    /**
     * PricingConstraint
     * @description A stable condition selecting one ordered model price rule.
     */
    PricingConstraint: {
      /** End Time */
      end_time?: string | null;
      /** @default always */
      kind?: components["schemas"]["PricingConstraintKind"];
      /** Start Date */
      start_date?: string | null;
      /** Start Time */
      start_time?: string | null;
      /**
       * Weekdays
       * @default []
       */
      weekdays?: number[];
    };
    /** @enum {string} */
    PricingConstraintKind: "always" | "start_date" | "daily_time";
    /** PrincipalSummary */
    PrincipalSummary: {
      /** Email */
      email: string | null;
      /** Id */
      id: string;
      /** Image Url */
      image_url: string | null;
      /** Kind */
      kind: string;
      /** Name */
      name: string;
      /** Status */
      status: string;
    };
    /** Profile */
    Profile: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Email */
      email: string | null;
      /** Id */
      id: string;
      /** Image Url */
      image_url: string | null;
      /** Kind */
      kind: string;
      /** Name */
      name: string;
      /** Status */
      status: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /** ProfileUpdate */
    ProfileUpdate: {
      /** Current Password */
      current_password?: string | null;
      /** Email */
      email?: string | null;
      /** Name */
      name?: string | null;
    };
    /** Provider */
    Provider: {
      /** Config */
      config: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string | null;
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      /** Header Names */
      header_names: string[];
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
      /** Updated By Id */
      updated_by_id: string | null;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** ProviderCreate */
    ProviderCreate: {
      /** Config */
      config?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential */
      credential?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Extra Headers */
      extra_headers?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Type */
      type: string;
    };
    /** ProviderPage */
    ProviderPage: {
      /** Items */
      items: components["schemas"]["Provider"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ProviderTest */
    ProviderTest: {
      /** Message */
      message: string | null;
      /** Provider Id */
      provider_id: string;
      /** Provider Version */
      provider_version: number;
      /**
       * Status
       * @enum {string}
       */
      status: "succeeded" | "failed" | "unsupported";
    };
    /** ProviderType */
    ProviderType: {
      authentication: components["schemas"]["Authentication"];
      /** Catalog Providers */
      catalog_providers?: string[] | null;
      /** Configuration Schema */
      configuration_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Credential Schema */
      credential_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Default Model Api */
      default_model_api?: string | null;
      /** Display Name */
      display_name: string;
      /** Environment Schema */
      environment_schema?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Model Api Labels */
      model_api_labels?: {
        [key: string]: string;
      } | null;
      /** Model Apis */
      model_apis?: string[] | null;
      /** Operations */
      operations?: components["schemas"]["WebOperation"][] | null;
      /** Settings Schemas */
      settings_schemas?: {
        [key: string]: {
          [key: string]: components["schemas"]["JsonValue"];
        };
      } | null;
      /** Setup Label */
      setup_label: string | null;
      /** Setup Url */
      setup_url: string | null;
      /** Supports Destroy */
      supports_destroy?: boolean | null;
      /** Supports Stop */
      supports_stop?: boolean | null;
      /** Supports Test */
      supports_test: boolean;
      /** Type */
      type: string;
    };
    /** ProviderTypePage */
    ProviderTypePage: {
      /** Items */
      items: components["schemas"]["ProviderType"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ProviderUpdate */
    ProviderUpdate: {
      /** Config */
      config?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Credential */
      credential?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Extra Headers */
      extra_headers?: {
        [key: string]: string | null;
      };
      /** Name */
      name?: string | null;
    };
    /**
     * Resume
     * @description The complete result batch, submitted and stored on the successor without omission defaults.
     */
    Resume: {
      /** Approvals */
      approvals: {
        [key: string]: components["schemas"]["ApprovalDecision"];
      };
      /** Calls */
      calls: {
        [key: string]: components["schemas"]["CallResult"];
      };
      input?: components["schemas"]["MessagePayload"] | null;
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
    /**
     * Returned
     * @description A JSON tool result. Built-in question values are validated by the Harness.
     */
    Returned: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      status: "returned";
      value: components["schemas"]["JsonValue"];
    };
    /** RevokedConnection */
    RevokedConnection: {
      auth: components["schemas"]["ConnectionAuth"];
      /** Authorization Pending */
      authorization_pending: boolean;
      /** Client Secret Configured */
      client_secret_configured: boolean;
      config: components["schemas"]["ConnectionConfig"];
      /** Connector Provider Id */
      connector_provider_id: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Credential Configured */
      credential_configured: boolean;
      /** Enabled */
      enabled: boolean;
      failure: components["schemas"]["ConnectionFailure"] | null;
      /** Id */
      id: string;
      last_test: components["schemas"]["ConnectionTestOutcome"] | null;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /**
       * Remote Revocation
       * @enum {string}
       */
      remote_revocation: "revoked" | "failed" | "skipped";
      status: components["schemas"]["ConnectionStatus"];
      /** Type */
      type: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /**
     * RunItems
     * @description A run's committed display with the run it describes. Live output continues after `position`.
     */
    RunItems: {
      /** Complete */
      complete: boolean;
      /** Dropped */
      dropped: number;
      /** Items */
      items: components["schemas"]["Item"][];
      /** Position */
      position: string | null;
      /** Resume After */
      resume_after?: string | null;
      run: components["schemas"]["RunView"];
    };
    /** RunLabels */
    RunLabels: {
      /** Labels */
      labels: {
        [key: string]: string;
      };
    };
    /** RunMetrics */
    RunMetrics: {
      /** Average Duration Seconds */
      average_duration_seconds: number | null;
      /** Runs */
      runs: number;
    };
    /**
     * RunOptions
     * @description What a message may choose for the run it starts. A steer joins a run with the defaults or equal options.
     */
    "RunOptions-Input": {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      max_usage?: components["schemas"]["UsageLimit"] | null;
      overrides?: components["schemas"]["AgentOverride-Input"] | null;
    };
    /**
     * RunOptions
     * @description What a message may choose for the run it starts. A steer joins a run with the defaults or equal options.
     */
    "RunOptions-Output": {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      max_usage?: components["schemas"]["UsageLimit"] | null;
      overrides?: components["schemas"]["AgentOverride-Output"] | null;
    };
    /** RunPage */
    RunPage: {
      /** Items */
      items: components["schemas"]["RunView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** @enum {string} */
    RunStatus:
      "accepted" | "running" | "waiting" | "completed" | "failed" | "cancelled";
    /** RunView */
    RunView: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Attempts */
      attempts: number;
      /** Cancel Requested At */
      cancel_requested_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Attempt Id */
      current_attempt_id: string | null;
      /** Environment Mounts */
      environment_mounts: components["schemas"]["EnvironmentMount"][];
      failure: components["schemas"]["Failure"] | null;
      /** Id */
      id: string;
      /** Input */
      input?: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      lineage: components["schemas"]["Lineage"];
      /** Max Attempts */
      max_attempts: number;
      /** Memory Mounts */
      memory_mounts: components["schemas"]["MemoryMount"][];
      options: components["schemas"]["RunOptions-Output"];
      output: components["schemas"]["JsonValue"] | null;
      /** Parent Run Id */
      parent_run_id: string | null;
      pending: components["schemas"]["Pending"] | null;
      /** Principal Id */
      principal_id: string;
      resume: components["schemas"]["Resume"] | null;
      /** Resumed By Id */
      resumed_by_id: string | null;
      /**
       * Revision Selection
       * @enum {string}
       */
      revision_selection: "pinned" | "default" | "inherited";
      /** Sealed At */
      sealed_at: string | null;
      /** Session Id */
      session_id: string;
      /** Source Entry Id */
      source_entry_id: string | null;
      /** Started At */
      started_at: string | null;
      status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
      trigger: components["schemas"]["Trigger"];
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Usage At Seal */
      usage_at_seal: {
        [key: string]: components["schemas"]["JsonValue"];
      } | null;
      /** Version */
      version: number;
      wait_reason: components["schemas"]["WaitReason"] | null;
      /** Workspace Id */
      workspace_id: string;
    };
    /** ServiceAccount */
    ServiceAccount: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Description */
      description: string;
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Role */
      role: string | null;
      /**
       * Status
       * @enum {string}
       */
      status: "active" | "disabled";
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
    /** ServiceAccountCreate */
    ServiceAccountCreate: {
      /**
       * Description
       * @default
       */
      description?: string;
      /** Name */
      name: string;
      /**
       * Role
       * @default runner
       */
      role?: string;
    };
    /** ServiceAccountPage */
    ServiceAccountPage: {
      /** Items */
      items: components["schemas"]["ServiceAccount"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ServiceAccountUpdate */
    ServiceAccountUpdate: {
      /** Description */
      description?: string | null;
      /** Name */
      name?: string | null;
      /** Role */
      role?: string | null;
      /** Status */
      status?: ("active" | "disabled") | null;
    };
    /** SessionCreate */
    SessionCreate: {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
    };
    /** SessionPage */
    SessionPage: {
      /** Items */
      items: components["schemas"]["SessionView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * SessionPreview
     * @description The session's latest run, summarized for a list row.
     */
    SessionPreview: {
      /** Agent Id */
      agent_id: string;
      /** Agent Name */
      agent_name: string;
      /** Input Text */
      input_text: string | null;
      /** Output Text */
      output_text: string | null;
      /** Run Id */
      run_id: string;
      status: components["schemas"]["RunStatus"];
      /** Thread Id */
      thread_id: string;
      trigger: components["schemas"]["Trigger"];
    };
    /**
     * SessionProfile
     * @description What a browser restores from its HttpOnly cookie: the account and the token it sends on mutations.
     */
    SessionProfile: {
      /** Csrf Token */
      csrf_token: string;
      user: components["schemas"]["Profile"];
    };
    /** SessionUpdate */
    SessionUpdate: {
      /** Labels */
      labels: {
        [key: string]: string;
      };
    };
    /** SessionView */
    SessionView: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Id */
      id: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Last Run Id */
      last_run_id: string | null;
      preview?: components["schemas"]["SessionPreview"] | null;
      /**
       * Run Count
       * @default 0
       */
      run_count?: number;
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
    /** Skill */
    Skill: {
      /** Archived At */
      archived_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      default_revision: components["schemas"]["SkillRevisionSummary"] | null;
      /** Default Revision Id */
      default_revision_id: string | null;
      /** Description */
      description: string;
      /** Id */
      id: string;
      /** Labels */
      labels: {
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
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /**
     * SkillCreate
     * @description `name` and `description` default to what the package's SKILL.md declares.
     */
    SkillCreate: {
      /** Description */
      description?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name?: string | null;
      /** Source */
      source:
        | components["schemas"]["UploadSource"]
        | components["schemas"]["GitHubSource"];
    };
    /** SkillFile */
    SkillFile: {
      /** Path */
      path: string;
      /** Size */
      size: number;
    };
    /**
     * SkillManifest
     * @description The frozen configuration of a skill revision: what its SKILL.md declares and the exact package bytes.
     */
    SkillManifest: {
      /** Description */
      description: string;
      /** Files */
      files: components["schemas"]["SkillFile"][];
      /** Name */
      name: string;
      /** Package Digest */
      package_digest: string;
      /** Package Size */
      package_size: number;
      /**
       * Root
       * @description The archive directory holding SKILL.md: "" or "<directory>/"
       */
      root: string;
      /**
       * Size
       * @description Expanded bytes of all files
       */
      size: number;
      /** Source */
      source:
        | components["schemas"]["UploadSource"]
        | components["schemas"]["GitHubSource"];
    };
    /** SkillPage */
    SkillPage: {
      /** Items */
      items: components["schemas"]["Skill"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SkillRevision */
    SkillRevision: {
      config: components["schemas"]["SkillManifest"];
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Digest */
      digest: string;
      /** Id */
      id: string;
      /** Note */
      note: string | null;
      /** Number */
      number: number;
      /** Skill Id */
      skill_id: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** SkillRevisionCreate */
    SkillRevisionCreate: {
      /**
       * Make Default
       * @default true
       */
      make_default?: boolean;
      /** Note */
      note?: string | null;
      /** Source */
      source:
        | components["schemas"]["UploadSource"]
        | components["schemas"]["GitHubSource"];
    };
    /** SkillRevisionPage */
    SkillRevisionPage: {
      /** Items */
      items: components["schemas"]["SkillRevision"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * SkillRevisionSummary
     * @description What a skill's representation shows of its default revision.
     */
    SkillRevisionSummary: {
      /** Id */
      id: string;
      /** Number */
      number: number;
      /** Source */
      source:
        | components["schemas"]["UploadSource"]
        | components["schemas"]["GitHubSource"];
    };
    /** SkillSelection */
    SkillSelection: {
      /** Revision Id */
      revision_id?: string | null;
      /** Skill Id */
      skill_id: string;
    };
    /** SkillUpdate */
    SkillUpdate: {
      /** Description */
      description?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      } | null;
      /** Name */
      name?: string | null;
    };
    /**
     * SkillValidate
     * @description A package to read and check as creating a skill or revision would, storing nothing.
     */
    SkillValidate: {
      /** Source */
      source:
        | components["schemas"]["UploadSource"]
        | components["schemas"]["GitHubSource"];
    };
    /**
     * Span
     * @description One span as the backend stored it; `attributes` are its OpenTelemetry attributes.
     *
     *     Fields a backend does not keep are null or empty.
     */
    Span: {
      /** Attributes */
      attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Cost Usd */
      cost_usd: string | null;
      /** Ended At */
      ended_at: string | null;
      /** Events */
      events: components["schemas"]["SpanEvent"][];
      /** Id */
      id: string;
      input: components["schemas"]["JsonValue"];
      /** Kind */
      kind: string;
      /** Level */
      level: string | null;
      /** Links */
      links: components["schemas"]["SpanLink"][];
      /** Model */
      model: string | null;
      /** Name */
      name: string;
      output: components["schemas"]["JsonValue"];
      /** Parent Id */
      parent_id: string | null;
      /** Resource Attributes */
      resource_attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      scope: components["schemas"]["InstrumentationScope"] | null;
      /** Source Url */
      source_url: string | null;
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
      /**
       * Status
       * @enum {string}
       */
      status: "ok" | "error";
      /** Status Message */
      status_message: string | null;
      /** Trace Id */
      trace_id: string;
      /** Usage */
      usage: {
        [key: string]: number;
      };
    };
    /**
     * SpanEvent
     * @description Something the span recorded at one moment, such as an exception.
     */
    SpanEvent: {
      /** Attributes */
      attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Name */
      name: string;
      /**
       * Timestamp
       * Format: date-time
       */
      timestamp: string;
    };
    /**
     * SpanLink
     * @description Another span this one relates to, in its own trace or another.
     */
    SpanLink: {
      /** Attributes */
      attributes: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Span Id */
      span_id: string;
      /** Trace Id */
      trace_id: string;
    };
    /** SpanPage */
    SpanPage: {
      /** Items */
      items: components["schemas"]["Span"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * SubagentOverride
     * @description Replaces the fields it sets of an edge; an edge the revision lacks sets at least `agent_id`.
     */
    "SubagentOverride-Input": {
      /** Agent Id */
      agent_id?: string | null;
      context?: components["schemas"]["DelegationContextPolicy"] | null;
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"] | null;
      /** Revision Id */
      revision_id?: string | null;
      usage_limits?: components["schemas"]["UsageLimits-Input"] | null;
    };
    /**
     * SubagentOverride
     * @description Replaces the fields it sets of an edge; an edge the revision lacks sets at least `agent_id`.
     */
    "SubagentOverride-Output": {
      /** Agent Id */
      agent_id?: string | null;
      context?: components["schemas"]["DelegationContextPolicy"] | null;
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"] | null;
      /** Revision Id */
      revision_id?: string | null;
      usage_limits?: components["schemas"]["UsageLimits-Output"] | null;
    };
    /** SubagentSelection */
    "SubagentSelection-Input": {
      /** Agent Id */
      agent_id: string;
      context?: components["schemas"]["DelegationContextPolicy"];
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"];
      /** Revision Id */
      revision_id?: string | null;
      usage_limits?: components["schemas"]["UsageLimits-Input"] | null;
    };
    /** SubagentSelection */
    "SubagentSelection-Output": {
      /** Agent Id */
      agent_id: string;
      context?: components["schemas"]["DelegationContextPolicy"];
      /** Description */
      description?: string | null;
      environment?: components["schemas"]["ChildEnvironmentPolicy"];
      /** Revision Id */
      revision_id?: string | null;
      usage_limits?: components["schemas"]["UsageLimits-Output"] | null;
    };
    /**
     * Submitted
     * @description A submission receipt: the entry and, when the thread was idle, the run it started.
     */
    Submitted: {
      entry: components["schemas"]["EntryView"];
      run: components["schemas"]["RunView"] | null;
      thread: components["schemas"]["ThreadView"];
    };
    /** Subscription */
    Subscription: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Enabled */
      enabled: boolean;
      filter: components["schemas"]["SubscriptionFilter"];
      /** Id */
      id: string;
      /** Kinds */
      kinds: string[];
      /** Name */
      name: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Id */
      updated_by_id: string;
      /** Url */
      url: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /**
     * SubscriptionCreate
     * @description Without `signing_secret` the service generates one; either way it is returned only by this request.
     */
    SubscriptionCreate: {
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      filter?: components["schemas"]["SubscriptionFilter"];
      /** Kinds */
      kinds: components["schemas"]["LifecycleKind"][];
      /** Name */
      name: string;
      /** Signing Secret */
      signing_secret?: string | null;
      /** Url */
      url: string;
    };
    /**
     * SubscriptionFilter
     * @description Absent fields match every run.
     */
    SubscriptionFilter: {
      /** Agent Id */
      agent_id?: string | null;
      /** Session Id */
      session_id?: string | null;
      /** Thread Id */
      thread_id?: string | null;
    };
    /** SubscriptionPage */
    SubscriptionPage: {
      /** Items */
      items: components["schemas"]["Subscription"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * SubscriptionUpdate
     * @description `signing_secret` replaces the secret for deliveries queued after this change.
     */
    SubscriptionUpdate: {
      /** Enabled */
      enabled?: boolean | null;
      filter?: components["schemas"]["SubscriptionFilter"] | null;
      /** Kinds */
      kinds?: components["schemas"]["LifecycleKind"][] | null;
      /** Name */
      name?: string | null;
      /** Signing Secret */
      signing_secret?: string | null;
      /** Url */
      url?: string | null;
    };
    /** Template */
    Template: {
      config: components["schemas"]["TemplateConfig"];
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string | null;
      /** Description */
      description: string | null;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Provider Id */
      provider_id: string;
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Updated By Id */
      updated_by_id: string | null;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** TemplateConfig */
    TemplateConfig: {
      /** Delete After Seconds */
      delete_after_seconds?: number | null;
      /** Recipe */
      recipe?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Stop After Seconds
       * @default 1800
       */
      stop_after_seconds?: number | null;
    };
    /** TemplateCreate */
    TemplateCreate: {
      config?: components["schemas"]["TemplateConfig"];
      /** Description */
      description?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      /** Provider Id */
      provider_id: string;
    };
    /** TemplatePage */
    TemplatePage: {
      /** Items */
      items: components["schemas"]["Template"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * TemplateUpdate
     * @description Fields left out stay unchanged; `description: null` clears it.
     *
     *     A new provider or recipe needs `write` on the provider and applies to environments created afterwards;
     *     existing ones keep what they were built with, and the idle policy applies to all of them. `enabled: false`
     *     refuses new environments, including reserved ones never created; created ones keep working.
     */
    TemplateUpdate: {
      config?: components["schemas"]["TemplateConfig"] | null;
      /** Description */
      description?: string | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      } | null;
      /** Name */
      name?: string | null;
      /** Provider Id */
      provider_id?: string | null;
    };
    /** TextPart */
    TextPart: {
      /** Text */
      text: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "text";
    };
    /** ThreadPage */
    ThreadPage: {
      /** Items */
      items: components["schemas"]["ThreadView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ThreadUpdate */
    ThreadUpdate: {
      /** Labels */
      labels?: {
        [key: string]: string;
      } | null;
      mcp_headers?: components["schemas"]["McpHeaders"] | null;
    };
    /** ThreadView */
    ThreadView: {
      /** Archived At */
      archived_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Run Id */
      current_run_id: string | null;
      /** Head Run Id */
      head_run_id: string | null;
      /** Id */
      id: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Last Run Id */
      last_run_id: string | null;
      /** Mcp Headers */
      mcp_headers: {
        [key: string]: {
          [key: string]: string;
        };
      };
      message_history: components["schemas"]["MessageHistory"];
      /**
       * Origin
       * @enum {string}
       */
      origin: "new" | "fork" | "child";
      /** Origin Run Id */
      origin_run_id: string | null;
      /** Origin Thread Id */
      origin_thread_id: string | null;
      /** Origin Tool Call Id */
      origin_tool_call_id: string | null;
      /** Session Id */
      session_id: string;
      /** Subagent */
      subagent: string | null;
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
    ToolCallId: string;
    /** ToolDefinition */
    ToolDefinition: {
      /** Config Schema */
      config_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Default Enabled */
      default_enabled: boolean;
      default_permission: components["schemas"]["ToolPermissionMode"];
      /** Deployment Supported */
      deployment_supported: boolean;
      /** Display Name */
      display_name: string;
      /** Execution Id */
      execution_id: string;
      /** Key */
      key: string;
      /** Model Name */
      model_name: string;
      resource_selector: components["schemas"]["ToolResourceSelector"] | null;
      /** Supported Permissions */
      supported_permissions: (
        "inherit" | "allow" | "ask" | "deny" | "review"
      )[];
    };
    /** ToolInfo */
    ToolInfo: {
      /** Annotations */
      annotations?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Description */
      description: string | null;
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
      /** Provider Version */
      provider_version?: string | null;
    };
    /** ToolPage */
    ToolPage: {
      /** Items */
      items: components["schemas"]["ToolInfo"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** @enum {string} */
    ToolPermissionMode: "allow" | "deny" | "ask" | "review";
    ToolPermissionSetting:
      components["schemas"]["ToolPermissionMode"] | "inherit";
    /**
     * ToolResourceSelector
     * @description The resource a tool needs before it can be enabled.
     */
    ToolResourceSelector: {
      /**
       * Kind
       * @constant
       */
      kind: "web_provider";
      operation: components["schemas"]["WebOperation"];
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
    /** ToolsetCatalog */
    ToolsetCatalog: {
      /** Items */
      items: components["schemas"]["ToolsetDefinition"][];
    };
    /** ToolsetDefinition */
    ToolsetDefinition: {
      /** Config Schema */
      config_schema: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Default Enabled */
      default_enabled: boolean;
      /** Display Name */
      display_name: string;
      key: components["schemas"]["ToolsetKey"];
      /** Tools */
      tools: components["schemas"]["ToolDefinition"][];
    };
    /** @enum {string} */
    ToolsetKey:
      "files" | "shell" | "web" | "memory" | "assets" | "configuration";
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
    /**
     * TraceBackend
     * @description The trace backend queries read; `type` is null when trace query is not configured.
     */
    TraceBackend: {
      /** Queryable Since */
      queryable_since: string | null;
      /** Type */
      type: ("langfuse" | "logfire") | null;
    };
    /** @enum {string} */
    Trigger: "input" | "queued" | "resume" | "child_result" | "spawned";
    /** Upload */
    Upload: {
      /** Content Type */
      content_type: string;
      /** Digest */
      digest: string;
      /** Filename */
      filename: string;
      /** Size */
      size: number;
      /** Upload Id */
      upload_id: string;
    };
    /**
     * UploadCreate
     * @description The multipart form: one file part named `file`.
     */
    UploadCreate: {
      /** File */
      file: Binary;
    };
    /**
     * UploadSource
     * @description A package archive staged through `/uploads`.
     */
    UploadSource: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "upload";
      /** Upload Id */
      upload_id: string;
    };
    /** UrlPart */
    UrlPart: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "url";
      /** Url */
      url: string;
    };
    /** UsageLimit */
    UsageLimit: {
      /** Requests */
      requests: number;
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
    /** UsageOverview */
    UsageOverview: {
      /** Daily */
      daily: components["schemas"]["DailyUsage"][];
      runs: components["schemas"]["RunMetrics"];
      usage: components["schemas"]["ModelMetrics"];
    };
    /** UsageSummary */
    UsageSummary: {
      /** Models */
      models: components["schemas"]["ModelUsage"][];
    };
    /** UserKeyCreate */
    UserKeyCreate: {
      /** Expires At */
      expires_at?: string | null;
      /** Name */
      name: string;
      /** Workspace Id */
      workspace_id: string;
    };
    /** @enum {string} */
    Verb: "read" | "run" | "write" | "admin";
    /** @enum {string} */
    WaitReason: "approval" | "call" | "multiple";
    /** @enum {string} */
    WebOperation: "search" | "scrape";
    /**
     * WebhookDelivery
     * @description One webhook outbox row; its ID is the delivery ID receivers deduplicate by.
     */
    WebhookDelivery: {
      /** Attempts */
      attempts: number;
      /**
       * Available At
       * Format: date-time
       */
      available_at: string;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Delivered At */
      delivered_at: string | null;
      /** Id */
      id: string;
      /** Last Error */
      last_error: string | null;
      /** Payload */
      payload: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Status
       * @enum {string}
       */
      status: "pending" | "delivered" | "dead";
      /** Subscription Id */
      subscription_id: string;
      /** Url */
      url: string;
    };
    /** Workspace */
    Workspace: {
      /** Archived At */
      archived_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Id */
      id: string;
      /** Image Url */
      image_url: string | null;
      /** Name */
      name: string;
      /** Organization Id */
      organization_id: string;
      /** Permissions */
      permissions: components["schemas"]["Verb"][];
      /** Settings */
      settings: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /** WorkspaceCreate */
    WorkspaceCreate: {
      /** Name */
      name: string;
    };
    /** WorkspacePage */
    WorkspacePage: {
      /** Items */
      items: components["schemas"]["Workspace"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** WorkspaceUpdate */
    WorkspaceUpdate: {
      /** Name */
      name?: string | null;
    };
  };
  responses: {
    /** @description The failure, in the one error envelope */
    Error: {
      headers: {
        "X-Request-Id"?: string;
        [name: string]: unknown;
      };
      content: {
        "application/json": components["schemas"]["ErrorEnvelope"];
      };
    };
  };
  parameters: never;
  requestBodies: never;
  headers: never;
  pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
  prepare_composer_api_v1_agent_composer_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["Agent"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_agents_api_v1_agents_get: {
    parameters: {
      query?: {
        label?: string[] | null;
        q?: string | null;
        archived?: boolean | null;
        source?: components["schemas"]["AgentSource"] | null;
        skill_id?: string | null;
        skill_revision_id?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["AgentPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_agent_api_v1_agents_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentCreate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  validate_revision_api_v1_agents_validate_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentValidate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_agent_api_v1_agents__agent_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_agent_api_v1_agents__agent_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentUpdate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  archive_agent_api_v1_agents__agent_id__archive_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_avatar_api_v1_agents__agent_id__avatar_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description The image */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "image/jpeg": Binary;
          "image/png": Binary;
          "image/webp": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  put_avatar_api_v1_agents__agent_id__avatar_put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "image/jpeg": Binary;
        "image/png": Binary;
        "image/webp": Binary;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_avatar_api_v1_agents__agent_id__avatar_delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  duplicate_agent_api_v1_agents__agent_id__duplicate_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentDuplicate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_revisions_api_v1_agents__agent_id__revisions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
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
          "application/json": components["schemas"]["AgentRevisionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_revision_api_v1_agents__agent_id__revisions_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AgentRevisionCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AgentRevision"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_revision_api_v1_agents__agent_id__revisions__revision_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
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
          "application/json": components["schemas"]["AgentRevision"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  set_default_api_v1_agents__agent_id__revisions__revision_id__set_default_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
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
          "application/json": components["schemas"]["Agent"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  unarchive_agent_api_v1_agents__agent_id__unarchive_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        agent_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_assets_api_v1_assets_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["AssetPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_asset_api_v1_assets_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AssetCreate"];
      };
    };
    responses: {
      /** @description The asset already created from this upload */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Asset"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Asset"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_asset_api_v1_assets__asset_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  retire_asset_api_v1_assets__asset_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  read_asset_content_api_v1_assets__asset_id__content_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        asset_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description The asset bytes, with their stored content type */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "*/*": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  bootstrap_administrator_api_v1_auth_bootstrap_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["BootstrapInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginOutput"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
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
      default: components["responses"]["Error"];
    };
  };
  confirm_email_change_api_v1_auth_email_change_confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EmailChangeConfirm"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  password_login_api_v1_auth_login_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["LoginInput"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginOutput"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
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
      default: components["responses"]["Error"];
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
        "application/json": components["schemas"]["PasswordReset"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  confirm_password_reset_api_v1_auth_password_reset_confirm_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["PasswordResetConfirm"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  session_profile_api_v1_auth_session_get: {
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
          "application/json": components["schemas"]["SessionProfile"];
        };
      };
      default: components["responses"]["Error"];
    };
  };
  list_connections_api_v1_connections_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ConnectionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_connection_api_v1_connections_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectionCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  complete_authorization_api_v1_connections_callback_get: {
    parameters: {
      query: {
        state: string;
        code?: string | null;
        error?: string | null;
        iss?: string | null;
        session_uri?: string | null;
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
          "application/json": components["schemas"]["CallbackOutcome"];
        };
      };
      /** @description Back to the return URL the authorization named */
      303: {
        headers: {
          [name: string]: unknown;
        };
        content?: never;
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_redirect_uri_api_v1_connections_redirect_uri_get: {
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
          "application/json": components["schemas"]["OAuthRedirect"];
        };
      };
      default: components["responses"]["Error"];
    };
  };
  get_connection_api_v1_connections__connection_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["Connection"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_connection_api_v1_connections__connection_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ConnectionUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Connection"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  authorize_connection_api_v1_connections__connection_id__authorize_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        connection_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AuthorizationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationResult"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  revoke_connection_api_v1_connections__connection_id__revoke_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["RevokedConnection"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_connection_api_v1_connections__connection_id__test_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ConnectionTest"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_tools_api_v1_connections__connection_id__tools_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ToolPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_providers_api_v1_connector_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ProviderPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_provider_api_v1_connector_providers_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_provider_api_v1_connector_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_provider_api_v1_connector_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_apps_api_v1_connector_providers__provider_id__apps_get: {
    parameters: {
      query?: {
        query?: string | null;
        limit?: number;
        cursor?: string | null;
        refresh?: boolean;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["ConnectorAppPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_app_api_v1_connector_providers__provider_id__apps__app__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
        app: string;
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
          "application/json": components["schemas"]["ConnectorApp"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_actions_api_v1_connector_providers__provider_id__apps__app__actions_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
        app: string;
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
          "application/json": components["schemas"]["ConnectorActionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_provider_api_v1_connector_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["ProviderTest"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_providers_api_v1_environment_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ProviderPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_provider_api_v1_environment_providers_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_provider_api_v1_environment_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_provider_api_v1_environment_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_provider_api_v1_environment_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["ProviderTest"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_templates_api_v1_environment_templates_get: {
    parameters: {
      query?: {
        label?: string[] | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["TemplatePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_template_api_v1_environment_templates_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TemplateCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Template"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_template_api_v1_environment_templates__template_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["Template"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_template_api_v1_environment_templates__template_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        template_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["TemplateUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Template"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_environments_api_v1_environments_get: {
    parameters: {
      query?: {
        status?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["EnvironmentPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_environment_api_v1_environments_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json":
          | components["schemas"]["ManagedEnvironmentCreate"]
          | components["schemas"]["ExternalTargetCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_environment_api_v1_environments__environment_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_environment_api_v1_environments__environment_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["EnvironmentView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_environment_api_v1_environments__environment_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        environment_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EnvironmentUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["EnvironmentView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  stop_environment_api_v1_environments__environment_id__stop_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["EnvironmentView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  accept_api_v1_invitations__invitation_id__accept_post: {
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
        "application/json": components["schemas"]["InvitationAccept"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["LoginOutput"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_mcp_servers_api_v1_mcp_servers_get: {
    parameters: {
      query?: {
        query?: string | null;
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
          "application/json": components["schemas"]["McpServerPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_media_defaults_api_v1_media_understanding_defaults_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["MediaDefaults"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  replace_media_defaults_api_v1_media_understanding_defaults_put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MediaUnderstandingSelection"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MediaDefaults"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_memories_api_v1_memories_get: {
    parameters: {
      query?: {
        label?: string[] | null;
        kind?: components["schemas"]["MemoryKind"] | null;
        type?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["MemoryPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_memory_api_v1_memories_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Memory"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_memory_api_v1_memories__memory_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
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
          "application/json": components["schemas"]["Memory"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_memory_api_v1_memories__memory_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_memory_api_v1_memories__memory_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Memory"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_files_api_v1_memories__memory_id__files_get: {
    parameters: {
      query?: {
        /** @description A directory ending in "/"; "" lists every file */
        prefix?: string;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
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
          "application/json": components["schemas"]["MemoryFilePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_file_api_v1_memories__memory_id__files_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryFileCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryFile"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  move_file_api_v1_memories__memory_id__files_move_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryFileMove"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryFile"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  read_file_api_v1_memories__memory_id__files__path__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
        path: string;
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
          "application/json": components["schemas"]["MemoryFile"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  replace_file_api_v1_memories__memory_id__files__path__put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
        path: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryFileReplace"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryFile"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_file_api_v1_memories__memory_id__files__path__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
        path: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_records_api_v1_memories__memory_id__records_get: {
    parameters: {
      query?: {
        limit?: number;
        /** @description The provider's own cursor */
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
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
          "application/json": components["schemas"]["MemoryRecordPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  add_record_api_v1_memories__memory_id__records_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryRecordText"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryRecordView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  search_records_api_v1_memories__memory_id__records_search_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryRecordSearch"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryRecordPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_record_api_v1_memories__memory_id__records__record_id__put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
        record_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryRecordText"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryRecordView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_record_api_v1_memories__memory_id__records__record_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
        record_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_revisions_api_v1_memories__memory_id__revisions_get: {
    parameters: {
      query?: {
        path?: string | null;
        run_id?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
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
          "application/json": components["schemas"]["MemoryRevisionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  purge_history_api_v1_memories__memory_id__revisions_delete: {
    parameters: {
      query: {
        path: string;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
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
          "application/json": components["schemas"]["HistoryPurge"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_revision_api_v1_memories__memory_id__revisions__seq__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
        seq: number;
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
          "application/json": components["schemas"]["MemoryRevisionDetail"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  restore_revision_api_v1_memories__memory_id__revisions__seq__restore_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        memory_id: string;
        seq: number;
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
          "application/json": components["schemas"]["MemoryFileState"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_providers_api_v1_memory_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ProviderPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_provider_api_v1_memory_providers_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_provider_api_v1_memory_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_provider_api_v1_memory_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_provider_api_v1_memory_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["ProviderTest"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_model_catalog_api_v1_model_catalog_get: {
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
          "application/json": components["schemas"]["ModelCatalog"];
        };
      };
      default: components["responses"]["Error"];
    };
  };
  list_providers_api_v1_model_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ProviderPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_provider_api_v1_model_providers_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_provider_api_v1_model_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_provider_api_v1_model_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_provider_api_v1_model_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["ProviderTest"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_models_api_v1_models_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ModelPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_model_api_v1_models_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ModelCreate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_model_api_v1_models__key__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        key: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_model_api_v1_models__key__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        key: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ModelUpdate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_organizations_api_v1_organizations_get: {
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
          "application/json": components["schemas"]["OrganizationPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_organization_api_v1_organizations__organization_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_organization_api_v1_organizations__organization_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["OrganizationUpdate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_organization_audit_events_api_v1_organizations__organization_id__audit_events_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization_id: string;
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
          "application/json": components["schemas"]["AuditPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_organization_grants_api_v1_organizations__organization_id__grants_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization_id: string;
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
          "application/json": components["schemas"]["GrantPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_organization_grant_api_v1_organizations__organization_id__grants_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["GrantCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["GrantView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_organization_grant_api_v1_organizations__organization_id__grants__grant_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
        grant_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  change_organization_grant_api_v1_organizations__organization_id__grants__grant_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
        grant_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["GrantUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["GrantView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_organization_icon_api_v1_organizations__organization_id__icon_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description The image */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "image/jpeg": Binary;
          "image/png": Binary;
          "image/webp": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  put_organization_icon_api_v1_organizations__organization_id__icon_put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "image/jpeg": Binary;
        "image/png": Binary;
        "image/webp": Binary;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_organization_icon_api_v1_organizations__organization_id__icon_delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        organization_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_organization_invitations_api_v1_organizations__organization_id__invitations_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization_id: string;
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
          "application/json": components["schemas"]["InvitationPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_organization_invitation_api_v1_organizations__organization_id__invitations_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["InvitationCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationReceipt"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  resend_organization_invitation_api_v1_organizations__organization_id__invitations__invitation_id__resend_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        organization_id: string;
        invitation_id: string;
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
          "application/json": components["schemas"]["InvitationReceipt"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  revoke_organization_invitation_api_v1_organizations__organization_id__invitations__invitation_id__revoke_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        organization_id: string;
        invitation_id: string;
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
          "application/json": components["schemas"]["Invitation"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_members_api_v1_organizations__organization_id__members_get: {
    parameters: {
      query?: {
        kind?: ("user" | "service_account") | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization_id: string;
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
          "application/json": components["schemas"]["MemberPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_organization_workspaces_api_v1_organizations__organization_id__workspaces_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        organization_id: string;
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
          "application/json": components["schemas"]["WorkspacePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_workspace_api_v1_organizations__organization_id__workspaces_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        organization_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["WorkspaceCreate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_provider_types_api_v1_provider_types__kind__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        kind: "model" | "environment" | "connector" | "web" | "memory";
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
          "application/json": components["schemas"]["ProviderTypePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_run_api_v1_runs__run_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
        content: {
          "application/json": components["schemas"]["RunView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_run_api_v1_runs__run_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["RunLabels"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  run_attempts_api_v1_runs__run_id__attempts_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
        content: {
          "application/json": components["schemas"]["Attempts"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_attempt_spans_api_v1_runs__run_id__attempts__attempt_id__trace_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
        attempt_id: string;
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
          "application/json": components["schemas"]["SpanPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  fork_run_api_v1_runs__run_id__fork_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Fork"];
      };
    };
    responses: {
      /** @description The replayed submission in its current state */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  interrupt_run_api_v1_runs__run_id__interrupt_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
        content: {
          "application/json": components["schemas"]["RunView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  run_items_api_v1_runs__run_id__items_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
        content: {
          "application/json": components["schemas"]["RunItems"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  run_lineage_api_v1_runs__run_id__lineage_get: {
    parameters: {
      query?: {
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
        content: {
          "application/json": components["schemas"]["RunPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  resume_run_api_v1_runs__run_id__resume_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Resume"];
      };
    };
    responses: {
      /** @description The existing successor run in its current state */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunView"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["RunView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_sessions_api_v1_sessions_get: {
    parameters: {
      query?: {
        /** @description A session or thread ID */
        q?: string | null;
        agent_id?: string | null;
        status?: components["schemas"]["RunStatus"][];
        trigger?: components["schemas"]["Trigger"][];
        updated_after?: string | null;
        updated_before?: string | null;
        label?: string[];
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["SessionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_session_api_v1_sessions_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SessionCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_session_api_v1_sessions__session_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["SessionView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_session_api_v1_sessions__session_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        session_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SessionUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SessionView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_skills_api_v1_skills_get: {
    parameters: {
      query?: {
        label?: string[] | null;
        q?: string | null;
        source?: ("upload" | "github") | null;
        archived?: boolean | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["SkillPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_skill_api_v1_skills_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SkillCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Skill"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  validate_package_api_v1_skills_validate_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SkillValidate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillManifest"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_skill_api_v1_skills__skill_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_skill_api_v1_skills__skill_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SkillUpdate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  archive_skill_api_v1_skills__skill_id__archive_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_revisions_api_v1_skills__skill_id__revisions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["SkillRevisionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_revision_api_v1_skills__skill_id__revisions_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        skill_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SkillRevisionCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SkillRevision"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_revision_api_v1_skills__skill_id__revisions__revision_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        skill_id: string;
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
          "application/json": components["schemas"]["SkillRevision"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  read_archive_api_v1_skills__skill_id__revisions__revision_id__content_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        skill_id: string;
        revision_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description The revision's package as a zip archive */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/zip": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  read_file_api_v1_skills__skill_id__revisions__revision_id__files__path__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        skill_id: string;
        revision_id: string;
        path: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description One file of the revision's package */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/octet-stream": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  set_default_revision_api_v1_skills__skill_id__revisions__revision_id__set_default_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        skill_id: string;
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
          "application/json": components["schemas"]["Skill"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  unarchive_skill_api_v1_skills__skill_id__unarchive_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_subscriptions_api_v1_subscriptions_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["SubscriptionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_subscription_api_v1_subscriptions_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SubscriptionCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["CreatedSubscription"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_subscription_api_v1_subscriptions__subscription_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["Subscription"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_subscription_api_v1_subscriptions__subscription_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_subscription_api_v1_subscriptions__subscription_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        subscription_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["SubscriptionUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Subscription"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_deliveries_api_v1_subscriptions__subscription_id__deliveries_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["DeliveryPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  redeliver_api_v1_subscriptions__subscription_id__deliveries__delivery_id__redeliver_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        subscription_id: string;
        delivery_id: string;
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
          "application/json": components["schemas"]["WebhookDelivery"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_threads_api_v1_threads_get: {
    parameters: {
      query?: {
        session_id?: string | null;
        label?: string[] | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ThreadPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_thread_api_v1_threads_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["NewThread"];
      };
    };
    responses: {
      /** @description The replayed submission in its current state */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_thread_api_v1_threads__thread_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["ThreadView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_thread_api_v1_threads__thread_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ThreadUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  archive_thread_api_v1_threads__thread_id__archive_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["ThreadView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_mounts_api_v1_threads__thread_id__environments_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["MountPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  add_mount_api_v1_threads__thread_id__environments_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MountCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MountView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  remove_mount_api_v1_threads__thread_id__environments__name__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        name: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_inbox_api_v1_threads__thread_id__inbox_get: {
    parameters: {
      query?: {
        status?: components["schemas"]["EntryStatus"][] | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["EntryPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  submit_message_api_v1_threads__thread_id__inbox_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Message"];
      };
    };
    responses: {
      /** @description The replayed submission in its current state */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  reorder_inbox_api_v1_threads__thread_id__inbox_order_put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["InboxOrder"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["ThreadView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_entry_api_v1_threads__thread_id__inbox__entry_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        entry_id: string;
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
          "application/json": components["schemas"]["EntryView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  withdraw_entry_api_v1_threads__thread_id__inbox__entry_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        entry_id: string;
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
          "application/json": components["schemas"]["Submitted"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  edit_entry_api_v1_threads__thread_id__inbox__entry_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        entry_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EntryUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Submitted"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_mounts_api_v1_threads__thread_id__memories_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["MemoryMountPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  add_mount_api_v1_threads__thread_id__memories_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryMount"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryMount"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  remove_mount_api_v1_threads__thread_id__memories__name__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        name: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_mount_api_v1_threads__thread_id__memories__name__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        name: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemoryMountUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemoryMount"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_thread_runs_api_v1_threads__thread_id__runs_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "application/json": components["schemas"]["RunPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  thread_stream_api_v1_threads__thread_id__stream_get: {
    parameters: {
      query?: {
        run?: string | null;
        position?: string | null;
      };
      header?: {
        "Last-Event-ID"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
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
          "text/event-stream": unknown;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_toolsets_api_v1_toolsets_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ToolsetCatalog"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_trace_backend_api_v1_trace_backend_get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["TraceBackend"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_traces_api_v1_traces_get: {
    parameters: {
      query?: {
        session_id?: string | null;
        thread_id?: string | null;
        run_id?: string | null;
        /** @description key:value, an exact root span attribute */
        attribute?: string[] | null;
        started_after?: string | null;
        started_before?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["SpanPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_trace_api_v1_traces__trace_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        trace_id: string;
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
          "application/json": components["schemas"]["Span"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_trace_spans_api_v1_traces__trace_id__spans_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        trace_id: string;
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
          "application/json": components["schemas"]["SpanPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_upload_api_v1_uploads_post: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "multipart/form-data": components["schemas"]["UploadCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Upload"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  summarize_usage_api_v1_usage_get: {
    parameters: {
      query?: {
        run_id?: string | null;
        thread_id?: string | null;
        session_id?: string | null;
        ingested_after?: string | null;
        ingested_before?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["UsageSummary"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  usage_agents_api_v1_usage_agents_get: {
    parameters: {
      query: {
        start: string;
        end: string;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["AgentUsagePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  usage_models_api_v1_usage_models_get: {
    parameters: {
      query: {
        start: string;
        end: string;
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ModelUsagePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  usage_overview_api_v1_usage_overview_get: {
    parameters: {
      query: {
        start: string;
        end: string;
        timezone?: string;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["UsageOverview"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_profile_api_v1_users_me_get: {
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
          "application/json": components["schemas"]["Profile"];
        };
      };
      default: components["responses"]["Error"];
    };
  };
  update_profile_api_v1_users_me_patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProfileUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Profile"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_account_audit_events_api_v1_users_me_audit_events_get: {
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
          "application/json": components["schemas"]["AuditPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  put_avatar_api_v1_users_me_avatar_put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "image/jpeg": Binary;
        "image/png": Binary;
        "image/webp": Binary;
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Profile"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_avatar_api_v1_users_me_avatar_delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
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
          "application/json": components["schemas"]["Profile"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  disable_account_api_v1_users_me_disable_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["AccountDisable"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_user_keys_api_v1_users_me_keys_get: {
    parameters: {
      query?: {
        workspace_id?: string | null;
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
          "application/json": components["schemas"]["ApiKeyPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_user_key_api_v1_users_me_keys_post: {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["UserKeyCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["IssuedKey"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  revoke_user_key_api_v1_users_me_keys__key_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_login_sessions_api_v1_users_me_login_sessions_get: {
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
          "application/json": components["schemas"]["LoginSessionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  revoke_login_session_api_v1_users_me_login_sessions__session_id__delete: {
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
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
        "application/json": components["schemas"]["PasswordChange"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_avatar_api_v1_users__user_id__avatar_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        user_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description The image */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "image/jpeg": Binary;
          "image/png": Binary;
          "image/webp": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_providers_api_v1_web_providers_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
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
          "application/json": components["schemas"]["ProviderPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_provider_api_v1_web_providers_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path?: never;
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_provider_api_v1_web_providers__provider_id__get: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_provider_api_v1_web_providers__provider_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Provider"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_provider_api_v1_web_providers__provider_id__test_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        provider_id: string;
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
          "application/json": components["schemas"]["ProviderTest"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_workspaces_api_v1_workspaces_get: {
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
          "application/json": components["schemas"]["WorkspacePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_workspace_api_v1_workspaces__workspace_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_workspace_api_v1_workspaces__workspace_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["WorkspaceUpdate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  archive_workspace_api_v1_workspaces__workspace_id__archive_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_workspace_audit_events_api_v1_workspaces__workspace_id__audit_events_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
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
          "application/json": components["schemas"]["AuditPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_workspace_grants_api_v1_workspaces__workspace_id__grants_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
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
          "application/json": components["schemas"]["GrantPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_workspace_grant_api_v1_workspaces__workspace_id__grants_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["GrantCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["GrantView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_workspace_grant_api_v1_workspaces__workspace_id__grants__grant_id__delete: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        grant_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  change_workspace_grant_api_v1_workspaces__workspace_id__grants__grant_id__patch: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        grant_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["GrantUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["GrantView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_workspace_icon_api_v1_workspaces__workspace_id__icon_get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody?: never;
    responses: {
      /** @description The image */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "image/jpeg": Binary;
          "image/png": Binary;
          "image/webp": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  put_workspace_icon_api_v1_workspaces__workspace_id__icon_put: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "image/jpeg": Binary;
        "image/png": Binary;
        "image/webp": Binary;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_workspace_icon_api_v1_workspaces__workspace_id__icon_delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_workspace_invitations_api_v1_workspaces__workspace_id__invitations_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
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
          "application/json": components["schemas"]["InvitationPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_workspace_invitation_api_v1_workspaces__workspace_id__invitations_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["InvitationCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["InvitationReceipt"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  resend_workspace_invitation_api_v1_workspaces__workspace_id__invitations__invitation_id__resend_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
        invitation_id: string;
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
          "application/json": components["schemas"]["InvitationReceipt"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  revoke_workspace_invitation_api_v1_workspaces__workspace_id__invitations__invitation_id__revoke_post: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
        invitation_id: string;
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
          "application/json": components["schemas"]["Invitation"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_workspace_keys_api_v1_workspaces__workspace_id__keys_get: {
    parameters: {
      query?: {
        principal_id?: string | null;
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
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
          "application/json": components["schemas"]["ApiKeyPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  revoke_workspace_key_api_v1_workspaces__workspace_id__keys__key_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_service_accounts_api_v1_workspaces__workspace_id__service_accounts_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
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
          "application/json": components["schemas"]["ServiceAccountPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_service_account_api_v1_workspaces__workspace_id__service_accounts_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ServiceAccountCreate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_service_account_api_v1_workspaces__workspace_id__service_accounts__account_id__get: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_service_account_api_v1_workspaces__workspace_id__service_accounts__account_id__delete: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_service_account_api_v1_workspaces__workspace_id__service_accounts__account_id__patch: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
      };
      path: {
        workspace_id: string;
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ServiceAccountUpdate"];
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
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_service_account_keys_api_v1_workspaces__workspace_id__service_accounts__account_id__keys_get: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
      };
      header?: never;
      path: {
        workspace_id: string;
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
          "application/json": components["schemas"]["ApiKeyPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_service_account_key_api_v1_workspaces__workspace_id__service_accounts__account_id__keys_post: {
    parameters: {
      query?: never;
      header?: never;
      path: {
        workspace_id: string;
        account_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["KeyCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["IssuedKey"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  health_healthz_get: {
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
  ready_readyz_get: {
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
          "application/json": unknown;
        };
      };
    };
  };
}

export type Binary = Blob | ReadableStream<Uint8Array>;
