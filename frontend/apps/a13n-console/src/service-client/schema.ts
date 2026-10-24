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
    post: operations["prepare_composer"];
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
     *     archived agents, or only open ones; `source=builtin` finds managed presets; the skill filters keep those
     *     with a revision pinning that skill or that skill revision.
     */
    get: operations["list_agents"];
    put?: never;
    /** Create Agent */
    post: operations["create_agent"];
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
     * Validate Agent
     * @description No content when creating a revision of the configuration would accept it, else the same `invalid_argument`
     *     error with the field's path relative to `config`; nothing is stored.
     */
    post: operations["validate_agent"];
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
    get: operations["get_agent"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Agent */
    patch: operations["update_agent"];
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
    post: operations["archive_agent"];
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
    /** Get Agent Avatar */
    get: operations["get_agent_avatar"];
    /** Put Agent Avatar */
    put: operations["put_agent_avatar"];
    post?: never;
    /** Delete Agent Avatar */
    delete: operations["delete_agent_avatar"];
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
    post: operations["duplicate_agent"];
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
    /** List Agent Revisions */
    get: operations["list_agent_revisions"];
    put?: never;
    /**
     * Create Agent Revision
     * @description A configuration that validates to the default revision's creates nothing and returns that revision.
     */
    post: operations["create_agent_revision"];
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
    /** Get Agent Revision */
    get: operations["get_agent_revision"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/agents/{agent_id}/revisions/{revision_id}/compare": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Compare Agent Revisions
     * @description Versioned unified diff of two revisions of this agent, optionally limited to one configuration section.
     */
    get: operations["compare_agent_revisions"];
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
    /** Set Default Agent Revision */
    post: operations["set_default_agent_revision"];
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
    post: operations["unarchive_agent"];
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
    get: operations["list_assets"];
    put?: never;
    /** Create Asset */
    post: operations["create_asset"];
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
    get: operations["get_asset"];
    put?: never;
    post?: never;
    /** Retire Asset */
    delete: operations["retire_asset"];
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
    get: operations["read_asset_content"];
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
    post: operations["bootstrap_administrator"];
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
    get: operations["auth_configuration"];
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
    post: operations["confirm_email_change"];
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
    post: operations["password_login"];
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
    post: operations["logout"];
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
    post: operations["request_password_reset"];
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
    post: operations["confirm_password_reset"];
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
    get: operations["session_profile"];
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
    get: operations["list_connections"];
    put?: never;
    /** Create Connection */
    post: operations["create_connection"];
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
    get: operations["complete_authorization"];
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
    get: operations["get_redirect_uri"];
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
    get: operations["get_connection"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Connection */
    patch: operations["update_connection"];
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
    post: operations["authorize_connection"];
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
    post: operations["revoke_connection"];
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
    post: operations["test_connection"];
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
    /** List Connection Tools */
    get: operations["list_connection_tools"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/tools/definitions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Define Connection Tools
     * @description Read up to five exact definitions. Oversized values provide a versioned fragment reference.
     */
    get: operations["define_connection_tools"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connections/{connection_id}/tools/index": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Index Connection Tools
     * @description Search full names and descriptions; definitions are read separately by exact name.
     */
    get: operations["index_connection_tools"];
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
    /** List connector providers */
    get: operations["list_connector_providers"];
    put?: never;
    /** Create connector provider */
    post: operations["create_connector_provider"];
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
    /** Get connector provider */
    get: operations["get_connector_provider"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update connector provider
     * @description Omitted credentials and extra headers keep their saved values, including when `config` changes.
     */
    patch: operations["update_connector_provider"];
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}/apps": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Connector Apps */
    get: operations["list_connector_apps"];
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
    /** Get Connector App */
    get: operations["get_connector_app"];
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
    /** List Connector Actions */
    get: operations["list_connector_actions"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}/apps/{app}/actions/definitions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Define Connector Actions
     * @description Read up to five exact definitions. Oversized values provide a versioned fragment reference.
     */
    get: operations["define_connector_actions"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/connector-providers/{provider_id}/apps/{app}/actions/index": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Index Connector Actions
     * @description Search full names and descriptions; definitions are read separately by exact name.
     */
    get: operations["index_connector_actions"];
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
    /** Test connector provider */
    post: operations["test_connector_provider"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/desktop-assistance/{assistance_id}/complete": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Complete Assistance */
    post: operations["complete_assistance"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/desktop-assistance/{assistance_id}/control": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Control Desktop */
    post: operations["control_desktop"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/desktop-assistance/{assistance_id}/disconnect": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Disconnect Desktop */
    post: operations["disconnect_desktop"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/desktop-assistance/{assistance_id}/request-control": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Request Control */
    post: operations["request_control"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/desktop-assistance/{assistance_id}/view": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** View Desktop */
    post: operations["view_desktop"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/devices": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Devices */
    get: operations["list_devices"];
    put?: never;
    /** Create Device */
    post: operations["create_device"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/devices/{device_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Device */
    get: operations["get_device"];
    put?: never;
    post?: never;
    /** Delete Device */
    delete: operations["delete_device"];
    options?: never;
    head?: never;
    /** Update Device */
    patch: operations["update_device"];
    trace?: never;
  };
  "/api/v1/devices/{device_id}/metrics/query": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Query Device Metrics */
    post: operations["query_device_metrics"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/devices/{device_id}/rotate-credential": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Rotate Device Credential */
    post: operations["rotate_device_credential"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environment-provider-options/vercel": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Vercel Provider Options
     * @description List accessible teams, or projects in a selected team, using an unsaved credential.
     */
    post: operations["vercel_provider_options"];
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
    /** List environment providers */
    get: operations["list_environment_providers"];
    put?: never;
    /** Create environment provider */
    post: operations["create_environment_provider"];
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
    /** Get environment provider */
    get: operations["get_environment_provider"];
    put?: never;
    post?: never;
    /** Delete Environment Provider */
    delete: operations["delete_environment_provider"];
    options?: never;
    head?: never;
    /**
     * Update environment provider
     * @description Omitted credentials and extra headers keep their saved values, including when `config` changes.
     */
    patch: operations["update_environment_provider"];
    trace?: never;
  };
  "/api/v1/environment-providers/{provider_id}/references": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Environment Provider References */
    get: operations["environment_provider_references"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    /** Test environment provider */
    post: operations["test_environment_provider"];
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
    /** List Environment Templates */
    get: operations["list_environment_templates"];
    put?: never;
    /** Create Environment Template */
    post: operations["create_environment_template"];
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
    /** Get Environment Template */
    get: operations["get_environment_template"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Environment Template */
    patch: operations["update_environment_template"];
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
    get: operations["list_environments"];
    put?: never;
    /**
     * Create Environment
     * @description Reserve a managed sandbox from a template (`creating`), or register an external envd target (`ready`).
     */
    post: operations["create_environment"];
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
    get: operations["get_environment"];
    put?: never;
    post?: never;
    /** Delete Environment */
    delete: operations["delete_environment"];
    options?: never;
    head?: never;
    /** Update Environment */
    patch: operations["update_environment"];
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/computer/check": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /**
     * Check Computer
     * @description Probe desktop readiness without capturing the screen, sending input or starting the instance.
     */
    post: operations["check_computer"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/creation": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Environment Creation */
    get: operations["get_environment_creation"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/desktop-preview": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Environment Desktop Preview */
    get: operations["get_environment_desktop_preview"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/metrics-binding": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    /** Select Metrics Source */
    put: operations["select_metrics_source"];
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/metrics/collector": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Collector Status */
    get: operations["get_collector_status"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/metrics/query": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Query Environment Metrics */
    post: operations["query_environment_metrics"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/environments/{environment_id}/retry-creation": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Retry Environment Creation */
    post: operations["retry_environment_creation"];
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
    post: operations["stop_environment"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/finding-agent": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Prepare Finding Agent */
    post: operations["prepare_finding_agent"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/finding-analyses": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Finding Analyses */
    get: operations["list_finding_analyses"];
    put?: never;
    /** Start Analysis */
    post: operations["start_analysis"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/findings": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Findings */
    get: operations["list_findings"];
    put?: never;
    /**
     * Create Finding
     * @description Record an unconfirmed finding with evidence and a suggestion; a stable source_key makes a retry safe.
     *     A Run's own submission names that Run as its source; an analysis cites only selected traces it has read.
     */
    post: operations["create_finding"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/findings/{finding_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Finding */
    get: operations["get_finding"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Finding */
    patch: operations["update_finding"];
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
    post: operations["accept"];
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
    get: operations["list_mcp_servers"];
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
    get: operations["get_media_defaults"];
    /**
     * Replace Media Defaults
     * @description Replaces all three kinds; each model must declare it understands its kind. Requires workspace admin.
     */
    put: operations["replace_media_defaults"];
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
    get: operations["list_memories"];
    put?: never;
    /** Create Memory */
    post: operations["create_memory"];
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
    get: operations["get_memory"];
    put?: never;
    post?: never;
    /** Delete Memory */
    delete: operations["delete_memory"];
    options?: never;
    head?: never;
    /** Update Memory */
    patch: operations["update_memory"];
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
    get: operations["list_files"];
    put?: never;
    /** Create File */
    post: operations["create_file"];
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
    post: operations["move_file"];
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
    get: operations["read_file"];
    /** Replace File */
    put: operations["replace_file"];
    post?: never;
    /** Delete File */
    delete: operations["delete_file"];
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
    get: operations["list_records"];
    put?: never;
    /** Add Record */
    post: operations["add_record"];
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
    post: operations["search_records"];
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
    put: operations["update_record"];
    post?: never;
    /** Delete Record */
    delete: operations["delete_record"];
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
    /** List Memory Revisions */
    get: operations["list_memory_revisions"];
    put?: never;
    post?: never;
    /**
     * Purge History
     * @description Delete every retained revision of one file path.
     */
    delete: operations["purge_history"];
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
    /** Get Memory Revision */
    get: operations["get_memory_revision"];
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
     * Restore Memory Revision
     * @description Set the path back to the content the change replaced; `If-Match` names the file there, if any.
     */
    post: operations["restore_memory_revision"];
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
    /** List memory providers */
    get: operations["list_memory_providers"];
    put?: never;
    /** Create memory provider */
    post: operations["create_memory_provider"];
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
    /** Get memory provider */
    get: operations["get_memory_provider"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update memory provider
     * @description Omitted credentials and extra headers keep their saved values, including when `config` changes.
     */
    patch: operations["update_memory_provider"];
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
    /** Test memory provider */
    post: operations["test_memory_provider"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-spaces": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Spaces */
    get: operations["list_spaces"];
    put?: never;
    /** Create Space */
    post: operations["create_space"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-spaces/{space_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Space */
    get: operations["get_space"];
    put?: never;
    post?: never;
    /** Delete Space */
    delete: operations["delete_space"];
    options?: never;
    head?: never;
    /** Update Space */
    patch: operations["update_space"];
    trace?: never;
  };
  "/api/v1/memory-spaces/{space_id}/organization": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Space Organization */
    get: operations["get_space_organization"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-spaces/{space_id}/organizations": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Space Organizations */
    get: operations["list_space_organizations"];
    put?: never;
    /** Organize Space */
    post: operations["organize_space"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/memory-spaces/{space_id}/sessions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Space Sessions */
    get: operations["list_space_sessions"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/metrics-dashboards": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Dashboards */
    get: operations["list_dashboards"];
    put?: never;
    /** Create Dashboard */
    post: operations["create_dashboard"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/metrics-dashboards/preset": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Dashboard Preset */
    get: operations["get_dashboard_preset"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/metrics-dashboards/{dashboard_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Dashboard */
    get: operations["get_dashboard"];
    put?: never;
    post?: never;
    /** Delete Dashboard */
    delete: operations["delete_dashboard"];
    options?: never;
    head?: never;
    /** Update Dashboard */
    patch: operations["update_dashboard"];
    trace?: never;
  };
  "/api/v1/metrics-data-sources": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Metrics Data Sources */
    get: operations["list_metrics_data_sources"];
    put?: never;
    /** Create Metrics Data Source */
    post: operations["create_metrics_data_source"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/metrics-data-sources/capabilities": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Metrics Capabilities */
    get: operations["get_metrics_capabilities"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/metrics-data-sources/deployment-preset": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Metrics Deployment Preset */
    get: operations["get_metrics_deployment_preset"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/metrics-data-sources/{source_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Metrics Data Source */
    get: operations["get_metrics_data_source"];
    put?: never;
    post?: never;
    /** Delete Metrics Data Source */
    delete: operations["delete_metrics_data_source"];
    options?: never;
    head?: never;
    /** Update Metrics Data Source */
    patch: operations["update_metrics_data_source"];
    trace?: never;
  };
  "/api/v1/metrics-data-sources/{source_id}/query": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Query Metrics Data Source */
    post: operations["query_metrics_data_source"];
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
     * @description Read public model metadata and prices. Filter by provider and model to avoid a large full-catalog response.
     *
     *     Both filters match exactly and combine with AND; omitted filters leave that dimension unrestricted.
     *     No match returns empty items with the catalog status. Refreshes always fetch the full catalog when due.
     *     Copy the selected item's characteristics, pricing and ref into model config, pricing and catalog_ref when saving.
     */
    get: operations["get_model_catalog"];
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
    /** List model providers */
    get: operations["list_model_providers"];
    put?: never;
    /** Create model provider */
    post: operations["create_model_provider"];
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
    /** Get model provider */
    get: operations["get_model_provider"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update model provider
     * @description Omitted credentials and extra headers keep their saved values, including when `config` changes.
     */
    patch: operations["update_model_provider"];
    trace?: never;
  };
  "/api/v1/model-providers/{provider_id}/authorization": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Model Authorization */
    get: operations["model_authorization"];
    put?: never;
    post?: never;
    /** Disconnect Model Authorization */
    delete: operations["disconnect_model_authorization"];
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/model-providers/{provider_id}/authorization/callback": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Complete Model Authorization */
    post: operations["complete_model_authorization"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/model-providers/{provider_id}/authorize": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Authorize Model */
    post: operations["authorize_model"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/model-providers/{provider_id}/models": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Discover Model Provider Models */
    get: operations["discover_model_provider_models"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    /** Test model provider */
    post: operations["test_model_provider"];
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
    get: operations["list_models"];
    put?: never;
    /**
     * Create Model
     * @description Needs `write` on the workspace and on the model's provider, whose credential the model spends.
     */
    post: operations["create_model"];
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
    get: operations["get_model"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Model
     * @description A configuration change also needs `write` on the model's provider.
     */
    patch: operations["update_model"];
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
    get: operations["list_organizations"];
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
    get: operations["get_organization"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Organization */
    patch: operations["update_organization"];
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
    get: operations["list_organization_audit_events"];
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
    get: operations["list_organization_grants"];
    put?: never;
    /** Create Organization Grant */
    post: operations["create_organization_grant"];
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
    delete: operations["delete_organization_grant"];
    options?: never;
    head?: never;
    /**
     * Change Organization Grant
     * @description The grant is replaced: the result carries its new ID.
     */
    patch: operations["change_organization_grant"];
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
    get: operations["get_organization_icon"];
    /** Put Organization Icon */
    put: operations["put_organization_icon"];
    post?: never;
    /** Delete Organization Icon */
    delete: operations["delete_organization_icon"];
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
    get: operations["list_organization_invitations"];
    put?: never;
    /** Create Organization Invitation */
    post: operations["create_organization_invitation"];
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
    post: operations["resend_organization_invitation"];
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
    post: operations["revoke_organization_invitation"];
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
    get: operations["list_members"];
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
    get: operations["list_organization_workspaces"];
    put?: never;
    /** Create Workspace */
    post: operations["create_workspace"];
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
    get: operations["list_provider_types"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Runs */
    get: operations["list_runs"];
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
    get: operations["get_run"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Run
     * @description Labels only.
     */
    patch: operations["update_run"];
    trace?: never;
  };
  "/api/v1/runs/{run_id}/agent": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Run Agent
     * @description The frozen revision and effective configuration. Section reads are versioned fragments, never writable configs.
     */
    get: operations["get_run_agent"];
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
    get: operations["list_run_attempts"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/attempts/{attempt_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Run Attempt */
    get: operations["get_run_attempt"];
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
    get: operations["list_attempt_spans"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/contents/{content_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Run Content
     * @description The complete value behind a committed display reference.
     */
    get: operations["get_run_content"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/desktop-assistance/{call_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Assistance */
    get: operations["get_assistance"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/desktop-control": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Control */
    get: operations["get_control"];
    put?: never;
    /** Open Control */
    post: operations["open_control"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/desktop-control/view": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Open View */
    post: operations["open_view"];
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
    post: operations["fork_run"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/images/{image_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Run Image
     * @description Read a retained screenshot from committed Run history. Binary media is not an MCP query endpoint.
     */
    get: operations["run_image"];
    put?: never;
    post?: never;
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
    post: operations["interrupt_run"];
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
    /**
     * List Run Items
     * @description A run's committed display items by ordinal. Detailed unfiltered reads default to the newest items and full tail.
     *     Compact or filtered reads start at the oldest ordinal and strictly honor limit. Use exclusive before/after
     *     positions, then follow next_before/next_after even when a filtered page is empty.
     */
    get: operations["list_run_items"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/runs/{run_id}/items/{item_id}/content": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Read Run Item Content
     * @description Read one display field or follow its retained content reference, under the run's existing authorization.
     */
    get: operations["read_run_item_content"];
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
     * Get Run Lineage
     * @description The run and its ancestors, nearest first, across fork origins.
     */
    get: operations["get_run_lineage"];
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
    post: operations["resume_run"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Schedules */
    get: operations["list_schedules"];
    put?: never;
    /**
     * Create Schedule
     * @description Save durable execution authority as the creator, beyond this credential or Run's lifetime.
     *
     *     Defaults to paused. Set state=enabled only when automatic future execution is intended.
     */
    post: operations["create_schedule"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules/preview": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Preview Schedule */
    post: operations["preview_schedule"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules/{schedule_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Schedule */
    get: operations["get_schedule"];
    put?: never;
    post?: never;
    /** Archive Schedule */
    delete: operations["archive_schedule"];
    options?: never;
    head?: never;
    /** Update Schedule */
    patch: operations["update_schedule"];
    trace?: never;
  };
  "/api/v1/schedules/{schedule_id}/dispatch": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Dispatch Schedule */
    post: operations["dispatch_schedule"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules/{schedule_id}/enable": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Enable Schedule */
    post: operations["enable_schedule"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules/{schedule_id}/occurrences": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Schedule Occurrences */
    get: operations["list_schedule_occurrences"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules/{schedule_id}/occurrences/{occurrence_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Schedule Occurrence */
    get: operations["get_schedule_occurrence"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules/{schedule_id}/pause": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Pause Schedule */
    post: operations["pause_schedule"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/schedules/{schedule_id}/status": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Schedule Status */
    get: operations["get_schedule_status"];
    put?: never;
    post?: never;
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
    get: operations["list_sessions"];
    put?: never;
    /** Create Session */
    post: operations["create_session"];
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
    get: operations["get_session"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Session */
    patch: operations["update_session"];
    trace?: never;
  };
  "/api/v1/sessions/{session_id}/message-authors": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Message Authors
     * @description Get message senders' current display names, emails, account kinds, status and avatar URLs.
     *
     *     Supply the session ID and 1 to 100 entry IDs from the thread inbox. Requires workspace read permission;
     *     only message entries in that session are returned. This is not an arbitrary user or member-directory lookup.
     *     Use returned names to identify senders in answers; retain IDs when needed for precise references.
     */
    get: operations["get_message_authors"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/sessions/{session_id}/stream": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Session Stream
     * @description The session's threads and the live output of their runs over SSE: `thread`, `items`, `delta` and `gap`
     *     frames, plus `schedule` and `occurrence` state for Schedule-owned sessions. Every connection starts from a snapshot.
     */
    get: operations["session_stream"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
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
    get: operations["list_skills"];
    put?: never;
    /** Create Skill */
    post: operations["create_skill"];
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
     * Validate Skill
     * @description The manifest the package would give a new skill or revision, checked as creation checks it; nothing is
     *     stored.
     */
    post: operations["validate_skill"];
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
    get: operations["get_skill"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update Skill
     * @description Name, description and labels; an archived skill changes only by unarchiving.
     */
    patch: operations["update_skill"];
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
    post: operations["archive_skill"];
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
    get: operations["list_skill_revisions"];
    put?: never;
    /**
     * Create Skill Revision
     * @description A package whose manifest equals the default revision's creates nothing and returns that revision.
     */
    post: operations["create_skill_revision"];
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
    /** Get Skill Revision */
    get: operations["get_skill_revision"];
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
     * Read Skill Archive
     * @description The revision's package as a zip archive.
     */
    get: operations["read_skill_archive"];
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
     * Read Skill File
     * @description One package file, by the path the revision's manifest lists.
     */
    get: operations["read_skill_file"];
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
    /** Set Default Skill Revision */
    post: operations["set_default_skill_revision"];
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
    post: operations["unarchive_skill"];
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
    get: operations["list_subscriptions"];
    put?: never;
    /**
     * Create Subscription
     * @description The response is the only time the signing secret is returned.
     */
    post: operations["create_subscription"];
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
    get: operations["get_subscription"];
    put?: never;
    post?: never;
    /** Delete Subscription */
    delete: operations["delete_subscription"];
    options?: never;
    head?: never;
    /** Update Subscription */
    patch: operations["update_subscription"];
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
    get: operations["list_deliveries"];
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
    post: operations["redeliver"];
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
    get: operations["list_threads"];
    put?: never;
    /**
     * Create Thread
     * @description Create a thread (and its session unless one is named) with its first message.
     */
    post: operations["create_thread"];
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
    get: operations["get_thread"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Thread */
    patch: operations["update_thread"];
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
    post: operations["archive_thread"];
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
    /** List Environment Mounts */
    get: operations["list_environment_mounts"];
    put?: never;
    /** Add Environment Mount */
    post: operations["add_environment_mount"];
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
    /** Remove Environment Mount */
    delete: operations["remove_environment_mount"];
    options?: never;
    head?: never;
    /** Update Environment Mount */
    patch: operations["update_environment_mount"];
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
    get: operations["list_inbox"];
    put?: never;
    /**
     * Submit Message
     * @description Append a message; it starts a run at once when the thread can accept it, or steers the active run.
     */
    post: operations["submit_message"];
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
    put: operations["reorder_inbox"];
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
     * Get Inbox Entry
     * @description One entry and its disposition; edits name the thread's ETag.
     */
    get: operations["get_inbox_entry"];
    put?: never;
    post?: never;
    /**
     * Withdraw Entry
     * @description Withdraw a pending entry; its tombstone keeps the request key.
     */
    delete: operations["withdraw_entry"];
    options?: never;
    head?: never;
    /** Edit Entry */
    patch: operations["edit_entry"];
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/memories": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Memory Mounts */
    get: operations["list_memory_mounts"];
    put?: never;
    /** Add Memory Mount */
    post: operations["add_memory_mount"];
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
    /** Remove Memory Mount */
    delete: operations["remove_memory_mount"];
    options?: never;
    head?: never;
    /** Update Memory Mount */
    patch: operations["update_memory_mount"];
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
     * @description Newest first, optionally starting at an anchor run; cursors page towards older or newer runs.
     */
    get: operations["list_thread_runs"];
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
     * @description Live output of one thread's runs over SSE: `delta` and `boundary` frames with `changed`, `reset`, `gap`.
     *     Resumes from `Last-Event-ID` or a run's `position`; the session stream suits clients that do not track them.
     */
    get: operations["thread_stream"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/tool-executions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** List Tool Executions */
    get: operations["list_tool_executions"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/tool-executions/{execution_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Tool Execution */
    get: operations["get_tool_execution"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/tool-executions/{execution_id}/cancel": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    get?: never;
    put?: never;
    /** Cancel Tool Execution */
    post: operations["cancel_tool_execution"];
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/tool-executions/{execution_id}/observation": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Observe Tool Execution */
    get: operations["observe_tool_execution"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/threads/{thread_id}/tool-executions/{execution_id}/result": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Get Tool Execution Result */
    get: operations["get_tool_execution_result"];
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
    get: operations["list_toolsets"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/toolsets/definitions": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Define Toolsets
     * @description Read up to five exact definitions. Oversized values provide a versioned fragment reference.
     */
    get: operations["define_toolsets"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/toolsets/index": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Index Toolsets
     * @description Search full names and descriptions; definitions are read separately by exact name.
     */
    get: operations["index_toolsets"];
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
    get: operations["get_trace_backend"];
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
    get: operations["list_traces"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/traces/search": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Search Traces
     * @description Rank and filter the entire root-start window before paging. Costs sum reported span costs, not billing.
     *
     *     Providers execute each query without a result cache; pages may reflect updates between requests.
     *     Keyset cursors bind the ordering, filters and last sort value with trace/span IDs.
     *     Incomplete queries fail without a partial rank.
     */
    get: operations["search_traces"];
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
    get: operations["get_trace"];
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
    get: operations["list_trace_spans"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/traces/{trace_id}/spans/{span_id}": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Get Trace Span
     * @description One complete observation, including redacted input, output and diagnostic metadata.
     */
    get: operations["get_trace_span"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/traces/{trace_id}/spans/{span_id}/content": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /**
     * Read Span Content
     * @description Read a fragment of one span's content. The backend is queried for that span alone; items is empty when the
     *     trace has no such span. Continue fragments with version and next_offset.
     */
    get: operations["read_span_content"];
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
    post: operations["create_upload"];
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
    get: operations["summarize_usage"];
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
    get: operations["usage_agents"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/usage/context": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Context Usage */
    get: operations["context_usage"];
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
    get: operations["usage_models"];
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
    get: operations["usage_overview"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    patch?: never;
    trace?: never;
  };
  "/api/v1/usage/records/{record_id}/metrics": {
    parameters: {
      query?: never;
      header?: never;
      path?: never;
      cookie?: never;
    };
    /** Model Request Metrics */
    get: operations["model_request_metrics"];
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
    get: operations["get_profile"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Profile */
    patch: operations["update_profile"];
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
    get: operations["list_account_audit_events"];
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
    /** Put User Avatar */
    put: operations["put_user_avatar"];
    post?: never;
    /** Delete User Avatar */
    delete: operations["delete_user_avatar"];
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
    post: operations["disable_account"];
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
    get: operations["list_user_keys"];
    put?: never;
    /**
     * Create User Key
     * @description Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation.
     */
    post: operations["create_user_key"];
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
    delete: operations["revoke_user_key"];
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
    get: operations["list_login_sessions"];
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
    delete: operations["revoke_login_session"];
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
    post: operations["change_password"];
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
    /** Get User Avatar */
    get: operations["get_user_avatar"];
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
    /** List web providers */
    get: operations["list_web_providers"];
    put?: never;
    /** Create web provider */
    post: operations["create_web_provider"];
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
    /** Get web provider */
    get: operations["get_web_provider"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /**
     * Update web provider
     * @description Omitted credentials and extra headers keep their saved values, including when `config` changes.
     */
    patch: operations["update_web_provider"];
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
    /** Test web provider */
    post: operations["test_web_provider"];
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
    get: operations["list_workspaces"];
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
    get: operations["get_workspace"];
    put?: never;
    post?: never;
    delete?: never;
    options?: never;
    head?: never;
    /** Update Workspace */
    patch: operations["update_workspace"];
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
    post: operations["archive_workspace"];
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
    get: operations["list_workspace_audit_events"];
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
    get: operations["list_workspace_grants"];
    put?: never;
    /** Create Workspace Grant */
    post: operations["create_workspace_grant"];
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
    delete: operations["delete_workspace_grant"];
    options?: never;
    head?: never;
    /**
     * Change Workspace Grant
     * @description The grant is replaced: the result carries its new ID.
     */
    patch: operations["change_workspace_grant"];
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
    get: operations["get_workspace_icon"];
    /** Put Workspace Icon */
    put: operations["put_workspace_icon"];
    post?: never;
    /** Delete Workspace Icon */
    delete: operations["delete_workspace_icon"];
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
    get: operations["list_workspace_invitations"];
    put?: never;
    /** Create Workspace Invitation */
    post: operations["create_workspace_invitation"];
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
    post: operations["resend_workspace_invitation"];
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
    post: operations["revoke_workspace_invitation"];
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
    get: operations["list_workspace_keys"];
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
    delete: operations["revoke_workspace_key"];
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
    get: operations["list_service_accounts"];
    put?: never;
    /** Create Service Account */
    post: operations["create_service_account"];
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
    get: operations["get_service_account"];
    put?: never;
    post?: never;
    /** Delete Service Account */
    delete: operations["delete_service_account"];
    options?: never;
    head?: never;
    /** Update Service Account */
    patch: operations["update_service_account"];
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
    get: operations["list_service_account_keys"];
    put?: never;
    /**
     * Create Service Account Key
     * @description Needs a login session: an API key never issues keys, so a leaked key cannot outlive its revocation.
     */
    post: operations["create_service_account_key"];
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
    get: operations["health"];
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
    get: operations["ready"];
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
    /** Access */
    Access: {
      assistance: components["schemas"]["View"];
      /** Expires In Seconds */
      expires_in_seconds: number;
      /**
       * Mode
       * @enum {string}
       */
      mode: "view" | "control";
      /** Password */
      password: string;
      /** Websocket Url */
      websocket_url: string;
    };
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
      preset_kind?: components["schemas"]["PresetKind"] | null;
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
       * Builtin Skills
       * @default []
       */
      builtin_skills?: "platform-operations"[];
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
      execution?: components["schemas"]["ExecutionOverride"] | null;
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      /**
       * Lazy Environment
       * @default true
       */
      lazy_environment?: boolean;
      media_understanding?: components["schemas"]["MediaUnderstandingSelection"];
      memory?: components["schemas"]["SessionMemorySettings"];
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
      /** Skill Sync */
      skill_sync?:
        | components["schemas"]["SharedSkillSync"]
        | components["schemas"]["IsolatedSkillSync"];
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
      tool_search?: components["schemas"]["ToolSearch"] | null;
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
       * Builtin Skills
       * @default []
       */
      builtin_skills?: "platform-operations"[];
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
      execution?: components["schemas"]["ExecutionOverride"] | null;
      /**
       * Instructions
       * @default
       */
      instructions?: string;
      /**
       * Lazy Environment
       * @default true
       */
      lazy_environment?: boolean;
      media_understanding?: components["schemas"]["MediaUnderstandingSelection"];
      memory?: components["schemas"]["SessionMemorySettings"];
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
      /** Skill Sync */
      skill_sync?:
        | components["schemas"]["SharedSkillSync"]
        | components["schemas"]["IsolatedSkillSync"];
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
      tool_search?: components["schemas"]["ToolSearch"] | null;
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
      /** Builtin Skills */
      builtin_skills?: "platform-operations"[] | null;
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connection Tools */
      connection_tools?: components["schemas"]["ConnectionSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      /** Lazy Environment */
      lazy_environment?: boolean | null;
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
      /** Skill Sync */
      skill_sync?:
        | (
            | components["schemas"]["SharedSkillSync"]
            | components["schemas"]["IsolatedSkillSync"]
          )
        | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentOverride-Input"] | null;
      } | null;
      tool_search?: components["schemas"]["ToolSearch"] | null;
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
      /** Builtin Skills */
      builtin_skills?: "platform-operations"[] | null;
      /** Client Tools */
      client_tools?: components["schemas"]["ClientToolDefinition"][] | null;
      /** Connection Tools */
      connection_tools?: components["schemas"]["ConnectionSelection"][] | null;
      /** Instructions */
      instructions?: string | null;
      /** Lazy Environment */
      lazy_environment?: boolean | null;
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
      /** Skill Sync */
      skill_sync?:
        | (
            | components["schemas"]["SharedSkillSync"]
            | components["schemas"]["IsolatedSkillSync"]
          )
        | null;
      /** Skills */
      skills?: components["schemas"]["SkillSelection"][] | null;
      /** Subagents */
      subagents?: {
        [key: string]: components["schemas"]["SubagentOverride-Output"] | null;
      } | null;
      tool_search?: components["schemas"]["ToolSearch"] | null;
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
    /** Analysis */
    Analysis: {
      /** Agent Id */
      agent_id: string | null;
      /** Cited Trace Count */
      cited_trace_count: number;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Finding Count */
      finding_count: number;
      /** Id */
      id: string;
      /** Read Trace Ids */
      read_trace_ids: string[];
      /** Run Id */
      run_id: string;
      /** Run Status */
      run_status: string;
      /** Selected Traces */
      selected_traces: components["schemas"]["SelectedTrace"][];
      selection: components["schemas"]["AnalysisCreate"];
      /** Selection Truncated */
      selection_truncated: boolean;
      /** Session Id */
      session_id: string;
      /** Thread Id */
      thread_id: string;
    };
    /** AnalysisCreate */
    AnalysisCreate: {
      /** Agent Id */
      agent_id?: string | null;
      /**
       * Max Traces
       * @default 10
       */
      max_traces?: number;
      /** Presets */
      presets?: components["schemas"]["Preset"][];
      /** Started After */
      started_after?: string | null;
      /** Started Before */
      started_before?: string | null;
      /** Trace Id */
      trace_id?: string | null;
    };
    /** AnalysisPage */
    AnalysisPage: {
      /** Items */
      items: components["schemas"]["Analysis"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** AnyChangeTrigger */
    AnyChangeTrigger: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "any_change";
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
    /** @enum {string} */
    Assessment:
      | "unreviewed"
      | "confirmed"
      | "expected"
      | "insufficient"
      | "false_positive";
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
    /** AuthorizationCallback */
    AuthorizationCallback: {
      /** Attempt Id */
      attempt_id: string;
      /**
       * Callback Url
       * Format: password
       */
      callback_url: string;
    };
    /** AuthorizationDisconnect */
    AuthorizationDisconnect: {
      /**
       * Local Tokens Cleared
       * @default true
       */
      local_tokens_cleared?: boolean;
      /** Revocation Confirmed */
      revocation_confirmed?: boolean | null;
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
    /** AuthorizationStart */
    AuthorizationStart: {
      /** Attempt Id */
      attempt_id: string;
      /** Authorization Url */
      authorization_url: string;
      /**
       * Expires At
       * Format: date-time
       */
      expires_at: string;
      /**
       * Method
       * @default manual_callback
       * @constant
       */
      method?: "manual_callback";
    };
    /** AuthorizationStatus */
    AuthorizationStatus: {
      /** Client Id */
      client_id?: string | null;
      /** Email */
      email?: string | null;
      /** Expires At */
      expires_at?: string | null;
      /** Message */
      message?: string | null;
      /**
       * Pending
       * @default false
       */
      pending?: boolean;
      /** Provider Id */
      provider_id: string;
      /**
       * State
       * @enum {string}
       */
      state: "disconnected" | "connected" | "reauthentication_required";
      /** Subject */
      subject?: string | null;
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
    /** BusyOccurrence */
    BusyOccurrence: {
      /** Entry Id */
      entry_id: string;
      /** Occurrence Id */
      occurrence_id: string;
      /** Root Thread Id */
      root_thread_id: string;
    };
    /** BusyOccurrencePage */
    BusyOccurrencePage: {
      /** Items */
      items: components["schemas"]["BusyOccurrence"][];
      /** Next Cursor */
      next_cursor?: string | null;
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
    /** Capabilities */
    Capabilities: {
      /** Collectors */
      collectors: components["schemas"]["CollectorCapability"][];
      /** Proxy */
      proxy: boolean;
    };
    /** CatalogIndex */
    CatalogIndex: {
      /** Items */
      items: components["schemas"]["CatalogIndexItem"][];
      /** Next Cursor */
      next_cursor: string | null;
      /**
       * Omitted Sections
       * @default [
       *       "definitions"
       *     ]
       */
      omitted_sections?: string[];
    };
    /** CatalogIndexItem */
    CatalogIndexItem: {
      /** Description */
      description: string;
      /**
       * Description Truncated
       * @default false
       */
      description_truncated?: boolean;
      /** Name */
      name: string;
      /** Provider Version */
      provider_version?: string | null;
      /** Supported Tool Count */
      supported_tool_count?: number | null;
      /** Tool Count */
      tool_count?: number | null;
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
    Category:
      | "unclear_request"
      | "instruction_issue"
      | "tool_design"
      | "tool_usage"
      | "tool_execution"
      | "answer_quality"
      | "context_gap"
      | "workflow_issue"
      | "boundary_violation";
    /** @enum {string} */
    Certainty: "not_dispatched" | "known" | "unknown";
    /** CgroupMetrics */
    CgroupMetrics: {
      cpu_max?: components["schemas"]["CpuLimit"] | null;
      /** Cpu Periods Total */
      cpu_periods_total?: number | null;
      /** Cpu System Usec */
      cpu_system_usec?: number | null;
      /** Cpu Throttled Periods Total */
      cpu_throttled_periods_total?: number | null;
      /** Cpu Throttled Usec */
      cpu_throttled_usec?: number | null;
      /** Cpu Usage Usec */
      cpu_usage_usec?: number | null;
      /** Cpu User Usec */
      cpu_user_usec?: number | null;
      io?: components["schemas"]["DiskMetrics"] | null;
      /** Memory Current Bytes */
      memory_current_bytes?: number | null;
      memory_events: components["schemas"]["MemoryEvents"];
      memory_max?: components["schemas"]["MemoryLimit"] | null;
      /** Pids Current */
      pids_current?: number | null;
      /** Pids Limit */
      pids_limit?: number | null;
      /** Pids Unlimited */
      pids_unlimited?: boolean | null;
      pressure: components["schemas"]["Pressures"];
    };
    /** ChangeCountTrigger */
    ChangeCountTrigger: {
      /** Count */
      count: number;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      type: "change_count";
    };
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
    /** @enum {string} */
    Collection: "native" | "pull" | "proxy" | "external";
    /** @enum {string} */
    Collector: "vmagent" | "alloy";
    /** CollectorCapability */
    CollectorCapability: {
      /** Available */
      available: boolean;
      kind: components["schemas"]["Collector"];
      /** Reason */
      reason?: ("not_configured" | "monitoring_disabled") | null;
    };
    /** CollectorStatus */
    CollectorStatus: {
      /** Checked At */
      checked_at?: number | null;
      collector?: components["schemas"]["Collector"] | null;
      /** State */
      state:
        | components["schemas"]["State"]
        | (
            | "not_selected"
            | "not_configured"
            | "pending"
            | "failed"
            | "unavailable"
            | "capacity"
          );
    };
    /** Completion */
    Completion: {
      /** Epoch */
      epoch: number;
      /**
       * Note
       * @default
       */
      note?: string;
      /**
       * Outcome
       * @default completed
       * @enum {string}
       */
      outcome?: "completed" | "abandoned";
      /** Session Id */
      session_id: string;
    };
    /**
     * ComputerCheck
     * @description A point-in-time capability probe, not an Agent or mount permission grant.
     */
    ComputerCheck: {
      /**
       * Display Count
       * @default 0
       */
      display_count?: number;
      /**
       * Input Ready
       * @default false
       */
      input_ready?: boolean;
      /**
       * Observe Ready
       * @default false
       */
      observe_ready?: boolean;
      /** Operations */
      operations?: components["schemas"]["EnvironmentAction"][];
      /**
       * Status
       * @enum {string}
       */
      status: "checked" | "unsupported" | "not_ready";
    };
    /** @enum {string} */
    ConfigSection:
      | "all"
      | "model"
      | "model_settings"
      | "model_characteristics"
      | "instructions"
      | "toolsets"
      | "skills"
      | "skill_sync"
      | "connection_tools"
      | "tool_search"
      | "builtin_skills"
      | "client_tools"
      | "user_questions"
      | "subagent_mode"
      | "subagents"
      | "reviewer"
      | "media_understanding"
      | "plugins"
      | "output_spec"
      | "retries"
      | "default_environment_template_id"
      | "lazy_environment"
      | "memory_mounts"
      | "memory";
    /** ConfigSummary */
    ConfigSummary: {
      /** Connection Count */
      connection_count: number;
      /** Model */
      model: string;
      /**
       * Omitted Sections
       * @default [
       *       "configuration"
       *     ]
       */
      omitted_sections?: string[];
      /** Skill Count */
      skill_count: number;
      /** Subagent Count */
      subagent_count: number;
      /** Toolsets */
      toolsets: string[];
    };
    /** Configuration */
    Configuration: {
      collection: components["schemas"]["Collection"];
      collector?: components["schemas"]["Collector"] | null;
      export?: components["schemas"]["Export"] | null;
      query?: components["schemas"]["Query"] | null;
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
    /** ConnectionMetrics */
    ConnectionMetrics: {
      /** Tcp States */
      tcp_states?: {
        [key: string]: number;
      };
      /** Tcp Total */
      tcp_total?: number | null;
      /** Udp Sockets */
      udp_sockets?: number | null;
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
     * @description An agent's selected tools and permissions.
     */
    ConnectionSelection: {
      /** Connection Id */
      connection_id: string;
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
    /**
     * ContentRef
     * @description An immutable Host-owned value, loaded through that Host's authorized content API.
     */
    ContentRef: {
      /** Id */
      id: string;
      /**
       * Media Type
       * @enum {string}
       */
      media_type: "text/plain" | "application/json";
      /** Preview */
      preview: string;
      /** Size Bytes */
      size_bytes: number;
      /**
       * Truncated
       * @default false
       */
      truncated?: boolean;
    };
    /** ContentSummary */
    ContentSummary: {
      /** Id */
      id: string;
      /** Media Type */
      media_type: string;
      /**
       * Omitted Sections
       * @default [
       *       "value"
       *     ]
       */
      omitted_sections?: string[];
      /** Size Chars */
      size_chars: number;
      /** Source Truncated */
      source_truncated: boolean;
      value: components["schemas"]["Preview"];
      /**
       * View
       * @default compact
       * @constant
       */
      view?: "compact";
    };
    /**
     * ContextUsage
     * @description A Run's configured window and its latest eligible generation, when available.
     */
    ContextUsage: {
      /** Context Window Tokens */
      context_window_tokens: number | null;
      /** Input Tokens */
      input_tokens: number | null;
      /** Output Tokens */
      output_tokens: number | null;
      /** Record Id */
      record_id: string | null;
      /** Run Id */
      run_id: string;
    };
    /** Control */
    Control: {
      /** Epoch */
      epoch: number;
      /** Session Id */
      session_id: string;
    };
    CpuLimit:
      | components["schemas"]["UnlimitedCpuLimit"]
      | components["schemas"]["LimitedCpuLimit"];
    /** CpuTimeSeconds */
    CpuTimeSeconds: {
      /** Idle */
      idle?: number | null;
      /** Iowait */
      iowait?: number | null;
      /** Irq */
      irq?: number | null;
      /** Nice */
      nice?: number | null;
      /** Softirq */
      softirq?: number | null;
      /** Steal */
      steal?: number | null;
      /** System */
      system?: number | null;
      /** User */
      user?: number | null;
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
    /** CreationProgress */
    CreationProgress: {
      /** Ended At */
      ended_at?: string | null;
      /**
       * Observed At
       * Format: date-time
       */
      observed_at: string;
      /**
       * Retryable
       * @default false
       */
      retryable?: boolean;
      /**
       * Stage
       * @enum {string}
       */
      stage:
        "preparing" | "building" | "starting" | "ready" | "failed" | "inactive";
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
    };
    /**
     * CredentialMode
     * @enum {string}
     */
    CredentialMode: "required" | "optional" | "forbidden";
    /** Credentials */
    Credentials: {
      /** Headers */
      headers?: {
        [key: string]: string;
      };
    };
    /** CronSchedule */
    CronSchedule: {
      /** Expression */
      expression: string;
      /** Timezone */
      timezone: string;
      /**
       * Type
       * @default cron
       * @constant
       */
      type?: "cron";
    };
    /** DaemonMetrics */
    DaemonMetrics: {
      /** Connections */
      connections: number;
      /** Processes */
      processes?: number | null;
      /** Retained Output Bytes */
      retained_output_bytes?: number | null;
      /** Sessions */
      sessions: number;
      /** Transfers */
      transfers?: number | null;
    };
    /** DailyUsage */
    DailyUsage: {
      /**
       * Date
       * Format: date
       */
      date: string;
      usage: components["schemas"]["ModelMetrics"];
    };
    /** Dashboard */
    Dashboard: {
      /** Builtin */
      builtin: boolean;
      config: components["schemas"]["DashboardConfig"];
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
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
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** DashboardConfig */
    DashboardConfig: {
      /** Panels */
      panels?: components["schemas"]["Panel"][];
      /**
       * Range Seconds
       * @default 3600
       */
      range_seconds?: number;
      /**
       * Refresh Seconds
       * @default 30
       * @enum {integer}
       */
      refresh_seconds?: 0 | 10 | 30 | 60 | 300;
      /**
       * Schema Version
       * @default 1
       * @constant
       */
      schema_version?: 1;
    };
    /** DashboardCreate */
    DashboardCreate: {
      config: components["schemas"]["DashboardConfig"];
      /** Name */
      name: string;
    };
    /** DashboardPage */
    DashboardPage: {
      /** Items */
      items: components["schemas"]["Dashboard"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** DashboardUpdate */
    DashboardUpdate: {
      config?: components["schemas"]["DashboardConfig"] | null;
      /** Name */
      name?: string | null;
    };
    /** DataSource */
    DataSource: {
      /** Builtin */
      builtin: boolean;
      config: components["schemas"]["Configuration"];
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Enabled */
      enabled: boolean;
      /** Has Export Credentials */
      has_export_credentials: boolean;
      /** Has Query Credentials */
      has_query_credentials: boolean;
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
      /** Updated By Id */
      updated_by_id: string;
      /** Version */
      version: number;
      /** Workspace Id */
      workspace_id: string;
    };
    /** DataSourceCreate */
    DataSourceCreate: {
      config: components["schemas"]["Configuration"];
      /** Deployment Preset Revision */
      deployment_preset_revision?: string | null;
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      export_credentials?: components["schemas"]["Credentials"] | null;
      /** Name */
      name: string;
      query_credentials?: components["schemas"]["Credentials"] | null;
      /**
       * Use Deployment Query Credentials
       * @default false
       */
      use_deployment_query_credentials?: boolean;
    };
    /** DataSourcePage */
    DataSourcePage: {
      /** Items */
      items: components["schemas"]["DataSource"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** DataSourceUpdate */
    DataSourceUpdate: {
      config?: components["schemas"]["Configuration"] | null;
      /** Enabled */
      enabled?: boolean | null;
      export_credentials?: components["schemas"]["Credentials"] | null;
      /** Name */
      name?: string | null;
      query_credentials?: components["schemas"]["Credentials"] | null;
    };
    /** Definition */
    Definition: {
      /** Definition */
      definition: {
        [key: string]: components["schemas"]["JsonValue"];
      };
      /** Name */
      name: string;
    };
    /** DefinitionReference */
    DefinitionReference: {
      /**
       * Instruction
       * @default Read this endpoint with one name, offset=0 and limit=4000, then continue with version and next_offset.
       */
      instruction?: string;
      /** Name */
      name: string;
      /** Size Chars */
      size_chars: number;
      /** Version */
      version: string;
    };
    /** Definitions */
    Definitions: {
      /** Items */
      items?: components["schemas"]["Definition"][];
      /** Missing */
      missing?: string[];
      /** Oversized */
      oversized?: components["schemas"]["DefinitionReference"][];
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
    /** DeploymentPreset */
    DeploymentPreset: {
      config: components["schemas"]["Configuration"];
      /** Has Query Credentials */
      has_query_credentials: boolean;
      /** Name */
      name: string;
      /** Revision */
      revision: string;
    };
    /** Device */
    Device: {
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Device Id */
      device_id: string;
      /** Enabled */
      enabled: boolean;
      /** Id */
      id: string;
      /** Metrics Data Source Id */
      metrics_data_source_id: string | null;
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
      workspace_id: string;
    };
    /** DeviceCreate */
    DeviceCreate: {
      /** Device Id */
      device_id: string;
      /** Metrics Data Source Id */
      metrics_data_source_id?: string | null;
      /** Name */
      name: string;
    };
    /** DeviceCredential */
    DeviceCredential: {
      device: components["schemas"]["Device"];
      /** Token */
      token: string;
    };
    /** DevicePage */
    DevicePage: {
      /** Items */
      items: components["schemas"]["Device"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** DeviceUpdate */
    DeviceUpdate: {
      /** Enabled */
      enabled?: boolean | null;
      /** Metrics Data Source Id */
      metrics_data_source_id?: string | null;
      /** Name */
      name?: string | null;
    };
    /** DiskIoMetrics */
    DiskIoMetrics: {
      /** Busy Time Ms */
      busy_time_ms?: number | null;
      /** Identity */
      identity: string;
      /** In Progress */
      in_progress?: number | null;
      /** Read Bytes */
      read_bytes?: number | null;
      /** Read Operations */
      read_operations?: number | null;
      /** Read Time Ms */
      read_time_ms?: number | null;
      /** Weighted Time Ms */
      weighted_time_ms?: number | null;
      /** Write Operations */
      write_operations?: number | null;
      /** Write Time Ms */
      write_time_ms?: number | null;
      /** Written Bytes */
      written_bytes?: number | null;
    };
    /** DiskMetrics */
    DiskMetrics: {
      /** Devices */
      devices?: {
        [key: string]: components["schemas"]["DiskIoMetrics"];
      };
      /** Truncated */
      truncated: boolean;
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
    /** EntrySummary */
    EntrySummary: {
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
      /**
       * Delivery
       * @enum {string}
       */
      delivery: "steer" | "next_run";
      failure: components["schemas"]["FailureSummary"] | null;
      /** Finished At */
      finished_at: string | null;
      /** Id */
      id: string;
      /**
       * Kind
       * @enum {string}
       */
      kind: "message" | "child_result";
      /**
       * Omitted Sections
       * @default [
       *       "payload",
       *       "options"
       *     ]
       */
      omitted_sections?: string[];
      /** Origin Run Id */
      origin_run_id: string | null;
      payload: components["schemas"]["Preview"];
      /** Position */
      position: number;
      /** Principal Id */
      principal_id: string;
      status: components["schemas"]["EntryStatus"];
      /** Thread Id */
      thread_id: string;
      /**
       * View
       * @default compact
       * @constant
       */
      view?: "compact";
    };
    /** EntrySummaryPage */
    EntrySummaryPage: {
      /** Items */
      items: components["schemas"]["EntrySummary"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
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
      kind: "message" | "child_result" | "tool_result";
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
     * EnvironmentAction
     * @description Exact authorization values in the locked first-party catalog.
     * @enum {string}
     */
    EnvironmentAction:
      | "environment.file.stat"
      | "environment.file.read_text"
      | "environment.file.read_bytes"
      | "environment.file.write_text"
      | "environment.file.patch_text"
      | "environment.file.list"
      | "environment.file.query"
      | "environment.file.search_text"
      | "environment.file.mkdir"
      | "environment.file.move"
      | "environment.file.remove"
      | "environment.file.write_bytes"
      | "environment.file.copy_source"
      | "environment.file.copy_destination"
      | "environment.shell.exec"
      | "environment.process.list"
      | "environment.process.start"
      | "environment.process.inspect"
      | "environment.process.read_output"
      | "environment.process.write_stdin"
      | "environment.process.close_stdin"
      | "environment.process.signal"
      | "environment.process.wait"
      | "environment.process.kill"
      | "environment.process.release"
      | "environment.output.read"
      | "environment.output.release"
      | "environment.port.inspect"
      | "environment.port.wait"
      | "environment.state.export"
      | "environment.state.restore"
      | "environment.computer.describe"
      | "environment.computer.observe"
      | "environment.computer.zoom"
      | "environment.computer.click"
      | "environment.computer.move"
      | "environment.computer.drag"
      | "environment.computer.scroll"
      | "environment.computer.type_text"
      | "environment.computer.press_keys"
      | "environment.computer.read_clipboard"
      | "environment.computer.write_clipboard";
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
      /**
       * @default {
       *       "operations": [
       *         "environment.file.copy_destination",
       *         "environment.file.copy_source",
       *         "environment.file.list",
       *         "environment.file.mkdir",
       *         "environment.file.move",
       *         "environment.file.patch_text",
       *         "environment.file.query",
       *         "environment.file.read_bytes",
       *         "environment.file.read_text",
       *         "environment.file.remove",
       *         "environment.file.search_text",
       *         "environment.file.stat",
       *         "environment.file.write_bytes",
       *         "environment.file.write_text",
       *         "environment.output.read",
       *         "environment.output.release",
       *         "environment.port.inspect",
       *         "environment.port.wait",
       *         "environment.process.close_stdin",
       *         "environment.process.inspect",
       *         "environment.process.kill",
       *         "environment.process.list",
       *         "environment.process.read_output",
       *         "environment.process.release",
       *         "environment.process.signal",
       *         "environment.process.start",
       *         "environment.process.wait",
       *         "environment.process.write_stdin",
       *         "environment.shell.exec",
       *         "environment.state.export",
       *         "environment.state.restore"
       *       ]
       *     }
       */
      permission_ceiling?: components["schemas"]["EnvironmentPermissionSet-Output"];
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
    /** EnvironmentPermissionSet */
    "EnvironmentPermissionSet-Input": {
      /**
       * Operations
       * @default []
       */
      operations?: components["schemas"]["EnvironmentAction"][];
    };
    /** EnvironmentPermissionSet */
    "EnvironmentPermissionSet-Output": {
      /** Operations */
      operations?: components["schemas"]["EnvironmentAction"][];
    };
    /** EnvironmentProviderReferences */
    EnvironmentProviderReferences: {
      /** Environment Count */
      environment_count: number;
      /** Environments */
      environments: components["schemas"]["ProviderReference"][];
      /** Template Count */
      template_count: number;
      /** Templates */
      templates: components["schemas"]["ProviderReference"][];
    };
    /** EnvironmentQuery */
    EnvironmentQuery: {
      /** End */
      end: number;
      /**
       * History
       * @default configured
       * @enum {string}
       */
      history?: "configured" | "temporary";
      /**
       * Include Snapshot
       * @default false
       */
      include_snapshot?: boolean;
      /** Metric */
      metric:
        | (
            | "system_cpu_busy_ratio"
            | "system_memory_used_bytes"
            | "cgroup_memory_current_bytes"
            | "system_memory_total_bytes"
            | "system_memory_available_bytes"
            | "system_memory_cache_bytes"
            | "system_memory_wired_bytes"
            | "system_memory_compressed_bytes"
            | "system_swap_used_bytes"
            | "system_swap_total_bytes"
            | "system_commit_total_bytes"
            | "system_commit_limit_bytes"
            | "system_processes"
            | "system_threads"
            | "system_handles"
            | "cgroup_pids_current"
            | "tcp_connections"
            | "udp_sockets"
            | "daemon_sessions"
            | "daemon_processes"
            | "daemon_connections"
            | "daemon_transfers"
            | "daemon_retained_output_bytes"
          )
        | (
            | "network_received_bytes_per_second"
            | "network_transmitted_bytes_per_second"
            | "disk_read_bytes_per_second"
            | "disk_written_bytes_per_second"
            | "disk_read_operations_per_second"
            | "disk_write_operations_per_second"
            | "cgroup_io_read_bytes_per_second"
            | "cgroup_io_written_bytes_per_second"
            | "cgroup_io_read_operations_per_second"
            | "cgroup_io_write_operations_per_second"
          );
      /** Start */
      start: number;
      /** Step */
      step: number;
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
    /** @description Default variables for Agent commands. Ordinary readable configuration, not secret storage. Command variables override these defaults. */
    EnvironmentVariables: {
      [key: string]: string;
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
      metrics_application?: components["schemas"]["MetricsApplication"] | null;
      /** Metrics Data Source Id */
      metrics_data_source_id?: string | null;
      /** Metrics Labels */
      metrics_labels?: {
        [key: string]: string;
      };
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
    /** Evidence */
    Evidence: {
      /** Run Id */
      run_id: string;
      /** Span Ids */
      span_ids?: string[];
      /** Trace Id */
      trace_id: string;
    };
    /** ExecutionObservation */
    ExecutionObservation: {
      execution: components["schemas"]["ExecutionView"];
      shell?: components["schemas"]["ShellPreview"] | null;
      /**
       * Source
       * @enum {string}
       */
      source: "live" | "retained" | "unavailable";
    };
    /**
     * ExecutionOverride
     * @description Sparse choices: null inherits; Thread PATCH distinguishes omission from null.
     */
    ExecutionOverride: {
      environment?: components["schemas"]["EnvironmentVariables"] | null;
      steering?: components["schemas"]["Steering"] | null;
      tools?: components["schemas"]["ToolExecutionOverride"] | null;
    };
    /** ExecutionPage */
    ExecutionPage: {
      /** Items */
      items: components["schemas"]["ExecutionView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ExecutionView
     * @description Run identity is the Service Run; attempt and worker identify the retained owner.
     */
    ExecutionView: {
      /** Attempt Id */
      attempt_id: string;
      /** Cancel Requested At */
      cancel_requested_at: string | null;
      /** Completed At */
      completed_at?: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at?: string;
      /**
       * Delivery Acknowledged
       * @default false
       */
      delivery_acknowledged?: boolean;
      /** Error */
      error?: string | null;
      /** Execution Id */
      execution_id: string;
      /** Expires At */
      expires_at: string | null;
      /** First Delivered At */
      first_delivered_at: string | null;
      /**
       * Foreground Released
       * @default false
       */
      foreground_released?: boolean;
      /**
       * Result Available
       * @default false
       */
      result_available?: boolean;
      /** Run Id */
      run_id: string;
      /**
       * Status
       * @default queued
       * @enum {string}
       */
      status?:
        "queued" | "running" | "succeeded" | "failed" | "cancelled" | "lost";
      /** Thread Id */
      thread_id: string;
      /** Tool Call Id */
      tool_call_id: string;
      /** Tool Name */
      tool_name: string;
      /** Worker Id */
      worker_id: string;
    };
    /** Export */
    Export: {
      /** Endpoint */
      endpoint: string;
      /**
       * Interval Seconds
       * @default 30
       */
      interval_seconds?: number;
      /**
       * Protocol
       * @default http/protobuf
       * @enum {string}
       */
      protocol?: "http/protobuf" | "grpc";
    };
    /** ExportHealth */
    ExportHealth: {
      /**
       * Dropped
       * @default 0
       */
      dropped?: number;
      /**
       * Exported
       * @default 0
       */
      exported?: number;
      /**
       * Failed
       * @default 0
       */
      failed?: number;
      /** Last Success At */
      last_success_at?: string | null;
      /**
       * Queued
       * @default 0
       */
      queued?: number;
    };
    /**
     * ExternalTargetCreate
     * @description An envd daemon someone runs, registered by its endpoint and the token it accepts.
     */
    ExternalTargetCreate: {
      /** Endpoint */
      endpoint: string;
      /** Metrics Data Source Id */
      metrics_data_source_id?: string | null;
      /** Metrics Labels */
      metrics_labels?: {
        [key: string]: string;
      };
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
    /** FailureSummary */
    FailureSummary: {
      /** Code */
      code: string;
      message: components["schemas"]["Preview"];
    };
    /** FilesystemInventory */
    FilesystemInventory: {
      /** Filesystems */
      filesystems?: {
        [key: string]: components["schemas"]["FilesystemObservation"];
      };
      /** Truncated */
      truncated: boolean;
    };
    /** FilesystemMetrics */
    FilesystemMetrics: {
      /** Available Bytes */
      available_bytes: number;
      /** Available Inodes */
      available_inodes?: number | null;
      /** Free Bytes */
      free_bytes: number;
      /** Free Inodes */
      free_inodes?: number | null;
      /** Total Bytes */
      total_bytes: number;
      /** Total Inodes */
      total_inodes?: number | null;
    };
    /** FilesystemObservation */
    FilesystemObservation: {
      metrics?: components["schemas"]["FilesystemMetrics"] | null;
    };
    /** Finding */
    Finding: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Analysis Id */
      analysis_id: string | null;
      assessment: components["schemas"]["Assessment"];
      /** Assessment Note */
      assessment_note: string;
      /** @description One primary issue type for the central evidenced claim, separate from analysis presets, impact and review. Put uncertainty in explanation/limitations; do not invent categories. */
      category: components["schemas"]["Category"];
      /** Closed */
      closed: boolean;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Evidence */
      evidence: components["schemas"]["Evidence"][];
      /** Explanation */
      explanation: string;
      /** Id */
      id: string;
      /**
       * Limitations
       * @default
       */
      limitations?: string;
      /** @default warning */
      severity?: components["schemas"]["Severity"];
      /** Source Key */
      source_key: string;
      /** Source Run Id */
      source_run_id: string | null;
      /** Suggestion */
      suggestion: string;
      /** Title */
      title: string;
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
    /** FindingCreate */
    FindingCreate: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** @description One primary issue type for the central evidenced claim, separate from analysis presets, impact and review. Put uncertainty in explanation/limitations; do not invent categories. */
      category: components["schemas"]["Category"];
      /** Evidence */
      evidence: components["schemas"]["Evidence"][];
      /** Explanation */
      explanation: string;
      /**
       * Limitations
       * @default
       */
      limitations?: string;
      /** @default warning */
      severity?: components["schemas"]["Severity"];
      /** Source Key */
      source_key: string;
      /** Suggestion */
      suggestion: string;
      /** Title */
      title: string;
    };
    /** FindingPage */
    FindingPage: {
      /** Items */
      items: components["schemas"]["Finding"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** FindingUpdate */
    FindingUpdate: {
      assessment?: components["schemas"]["Assessment"] | null;
      /** Assessment Note */
      assessment_note?: string | null;
      /** Closed */
      closed?: boolean | null;
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
     * FrozenMemoryMount
     * @description Host-resolved Space identity and roots, never accepted from a mount request.
     */
    FrozenMemoryMount: {
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
      /**
       * Roots
       * @default []
       */
      roots?: string[];
      /** Space Id */
      space_id?: string | null;
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
      /** @description Image preparation policy; omitted uses native defaults, null disables automatic preparation. */
      image_input?: components["schemas"]["ImageInputPolicy"] | null;
      /**
       * Proactive Context Management Threshold
       * @default 0.65
       */
      proactive_context_management_threshold?: number | null;
      url_input?: components["schemas"]["UrlInputSupport-Input"];
      video_input?: components["schemas"]["VideoInputPolicy"];
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
      /** @description Image preparation policy; omitted uses native defaults, null disables automatic preparation. */
      image_input?: components["schemas"]["ImageInputPolicy"] | null;
      /**
       * Proactive Context Management Threshold
       * @default 0.65
       */
      proactive_context_management_threshold?: number | null;
      url_input?: components["schemas"]["UrlInputSupport-Output"];
      video_input?: components["schemas"]["VideoInputPolicy"];
    };
    /** HeadersCredential */
    HeadersCredential: {
      /** Headers */
      headers: {
        [key: string]: string;
      };
    };
    /** History */
    History: {
      /** Available From */
      available_from: number | null;
      /** Available Until */
      available_until: number | null;
      /**
       * Inventory Truncated
       * @default false
       */
      inventory_truncated?: boolean;
      /**
       * Omitted Sources
       * @default 0
       */
      omitted_sources?: number;
      /** Resolution Seconds */
      resolution_seconds: number;
      /** Retention Seconds */
      retention_seconds: number;
      /** Series */
      series: components["schemas"]["Series"][];
      /**
       * Source
       * @default temporary
       * @constant
       */
      source?: "temporary";
      /**
       * Temporary
       * @default true
       */
      temporary?: boolean;
    };
    /** HistoryPurge */
    HistoryPurge: {
      /** Purged */
      purged: number;
    };
    /**
     * ImageInputPolicy
     * @description Preparation limits for one model's image input, not native ModelSettings.
     */
    ImageInputPolicy: {
      /**
       * Image Retention Ratio
       * @description Fraction of max_images retained at the first overflow; one evicts only the excess images.
       * @default 0.6
       */
      image_retention_ratio?: number;
      /**
       * Image Split Max Height
       * @default 4096
       */
      image_split_max_height?: number;
      /**
       * Image Split Overlap
       * @default 50
       */
      image_split_overlap?: number;
      /**
       * Max Image Bytes
       * @description Maximum base64-encoded bytes per image; zero disables this byte limit.
       * @default 5242880
       */
      max_image_bytes?: number;
      /**
       * Max Image Dimension
       * @description Maximum image axis; zero disables this limit.
       * @default 8000
       */
      max_image_dimension?: number;
      /**
       * Max Images
       * @description Maximum retained images; zero removes all image input.
       * @default 20
       */
      max_images?: number;
      /**
       * Split Large Images
       * @default true
       */
      split_large_images?: boolean;
      /**
       * Support Gif
       * @default true
       */
      support_gif?: boolean;
    };
    /** InboxOrder */
    InboxOrder: {
      /** Entry Ids */
      entry_ids: string[];
    };
    InitialMounts: components["schemas"]["MountCreate"][];
    /** InitialRun */
    InitialRun: {
      /** Id */
      id: string;
      status: components["schemas"]["RunStatus"];
    };
    /** InstantQuery */
    InstantQuery: {
      /** Expression */
      expression: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "instant";
      /** Time */
      time?: number | null;
    };
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
    /**
     * IsolatedSkillSync
     * @description Reuse immutable package directories, with a soft count budget.
     */
    IsolatedSkillSync: {
      /**
       * Cache Count
       * @default 128
       */
      cache_count?: number;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      mode: "isolated";
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
      /** Content Refs */
      content_refs?: {
        [key: string]: components["schemas"]["ContentRef"];
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
      /** Ordinal */
      ordinal: number;
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
    /** ItemPage */
    ItemPage: {
      /** Complete */
      complete: boolean;
      /** Items */
      items: (
        components["schemas"]["ItemSummary"] | components["schemas"]["Item"]
      )[];
      /** Next After */
      next_after?: number | null;
      /** Next Before */
      next_before?: number | null;
      run: components["schemas"]["RunSummary"];
      /**
       * View
       * @enum {string}
       */
      view: "compact" | "detailed";
    };
    /** @enum {string} */
    ItemState: "in_progress" | "completed" | "interrupted" | "failed";
    /** ItemSummary */
    ItemSummary: {
      arguments: components["schemas"]["Preview"];
      /** Ended At */
      ended_at: string | null;
      /** Id */
      id: string;
      kind: components["schemas"]["ItemKind"];
      /**
       * Omitted Sections
       * @default [
       *       "content",
       *       "content_refs"
       *     ]
       */
      omitted_sections?: string[];
      /** Ordinal */
      ordinal: number;
      result: components["schemas"]["Preview"];
      /** Source Truncated */
      source_truncated: boolean;
      /** Source Truncated Fields */
      source_truncated_fields: string[];
      /**
       * Started At
       * Format: date-time
       */
      started_at: string;
      state: components["schemas"]["ItemState"];
      text: components["schemas"]["Preview"];
      /** Tool Call Id */
      tool_call_id: string | null;
      /** Tool Name */
      tool_name: string | null;
    };
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
      | "run_attempt.cancelled"
      | "schedule.enabled"
      | "schedule.paused"
      | "schedule.completed"
      | "schedule.archived"
      | "schedule_occurrence.submitted"
      | "schedule_occurrence.skipped"
      | "schedule_occurrence.failed"
      | "schedule_occurrence.cancelled"
      | "schedule_occurrence.completed";
    /** LimitedCpuLimit */
    LimitedCpuLimit: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "limited";
      /** Period Usec */
      period_usec: number;
      /** Quota Usec */
      quota_usec: number;
    };
    /** LimitedMemoryLimit */
    LimitedMemoryLimit: {
      /** Bytes */
      bytes: number;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "limited";
    };
    /** @enum {string} */
    Lineage: "root" | "continue" | "fork";
    /** LocalResult */
    LocalResult: {
      export?: components["schemas"]["ExportHealth"] | null;
      history: components["schemas"]["History"];
      observation: components["schemas"]["Observation"];
      /**
       * Source
       * @default temporary
       * @constant
       */
      source?: "temporary";
    };
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
      /** Metrics Data Source Id */
      metrics_data_source_id?: string | null;
      /** Metrics Labels */
      metrics_labels?: {
        [key: string]: string;
      };
      /** Name */
      name?: string | null;
      /** Template Id */
      template_id: string;
    };
    /** @enum {string} */
    McpAuth: "none" | "bearer" | "headers" | "oauth";
    /** McpConfig */
    McpConfig: {
      /** Forward Context Headers */
      forward_context_headers?: boolean;
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
    /** MemoryEvents */
    MemoryEvents: {
      /** High */
      high?: number | null;
      /** Low */
      low?: number | null;
      /** Max */
      max?: number | null;
      /** Oom */
      oom?: number | null;
      /** Oom Group Kill */
      oom_group_kill?: number | null;
      /** Oom Kill */
      oom_kill?: number | null;
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
    MemoryLimit:
      | components["schemas"]["UnlimitedMemoryLimit"]
      | components["schemas"]["LimitedMemoryLimit"];
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
    /** MemoryRecordProvenance */
    MemoryRecordProvenance: {
      /** Content Matches */
      content_matches: boolean;
      created: components["schemas"]["MemoryRecordSource"] | null;
      last_write: components["schemas"]["MemoryRecordSource"];
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
    /**
     * MemoryRecordSource
     * @description A platform-confirmed write, not an asserted source message or complete provider history.
     */
    MemoryRecordSource: {
      /** Attempt Id */
      attempt_id: string | null;
      /**
       * Confirmed At
       * Format: date-time
       */
      confirmed_at: string;
      /** Principal Id */
      principal_id: string | null;
      /** Principal Name */
      principal_name?: string | null;
      /** Run Id */
      run_id: string | null;
      /** Tool Call Id */
      tool_call_id: string | null;
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
      provenance?: components["schemas"]["MemoryRecordProvenance"] | null;
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
    /** MemorySpace */
    MemorySpace: {
      /** Always Load */
      always_load: string[];
      /** Content Bytes */
      content_bytes: number;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Description */
      description: string | null;
      /** Enabled */
      enabled: boolean;
      /** File Count */
      file_count: number;
      /** Guide */
      guide: string | null;
      /** History Bytes */
      history_bytes: number;
      /** Id */
      id: string;
      /** Labels */
      labels: {
        [key: string]: string;
      };
      /** Memory Id */
      memory_id: string;
      /** Name */
      name: string;
      organization: components["schemas"]["OrganizationPolicy"];
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
    /** MemorySpaceCreate */
    MemorySpaceCreate: {
      /**
       * Always Load
       * @default [
       *       "global/MEMORY.md"
       *     ]
       */
      always_load?: string[];
      /** Description */
      description?: string | null;
      /**
       * Enabled
       * @default true
       */
      enabled?: boolean;
      /** Guide */
      guide?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Name */
      name: string;
      organization?: components["schemas"]["OrganizationPolicy"];
    };
    /** MemorySpacePage */
    MemorySpacePage: {
      /** Items */
      items: components["schemas"]["MemorySpace"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** MemorySpaceUpdate */
    MemorySpaceUpdate: {
      /** Always Load */
      always_load?: string[] | null;
      /** Description */
      description?: string | null;
      /** Enabled */
      enabled?: boolean | null;
      /** Guide */
      guide?: string | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      } | null;
      /** Name */
      name?: string | null;
      organization?: components["schemas"]["OrganizationPolicy"] | null;
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
    /** MessageAuthor */
    MessageAuthor: {
      /** Entry Id */
      entry_id: string;
      principal: components["schemas"]["PrincipalSummary"] | null;
      /** Principal Id */
      principal_id: string;
      /**
       * Submitted At
       * Format: date-time
       */
      submitted_at: string;
    };
    /** MessageAuthors */
    MessageAuthors: {
      /** Items */
      items: components["schemas"]["MessageAuthor"][];
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
    /**
     * MetricsApplication
     * @description The frozen sampler/exporter selection; applied_at confirms creation, not delivery.
     */
    MetricsApplication: {
      /** Applied At */
      applied_at?: string | null;
      /**
       * Collection
       * @enum {string}
       */
      collection: "native" | "proxy" | "pull";
      /** Sample Interval Ms */
      sample_interval_ms: number;
      /** Source Id */
      source_id: string;
      /** Source Version */
      source_version: number;
    };
    /** MetricsSample */
    MetricsSample: {
      cgroup_v2?: components["schemas"]["CgroupMetrics"] | null;
      /** Collection Duration Ms */
      collection_duration_ms: number;
      connections?: components["schemas"]["ConnectionMetrics"] | null;
      daemon?: components["schemas"]["DaemonMetrics"] | null;
      disks?: components["schemas"]["DiskMetrics"] | null;
      /** Filesystems */
      filesystems?: {
        [key: string]: components["schemas"]["FilesystemObservation"];
      };
      local_filesystems?: components["schemas"]["FilesystemInventory"] | null;
      network?: components["schemas"]["NetworkMetrics"] | null;
      /** Sample Age Ms */
      sample_age_ms: number;
      /**
       * Sampled At
       * Format: date-time
       */
      sampled_at: string;
      /** Sequence */
      sequence: number;
      system: components["schemas"]["SystemMetrics"];
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
    /** ModelCatalogPage */
    ModelCatalogPage: {
      /** Items */
      items: (
        | components["schemas"]["ModelSummary"]
        | components["schemas"]["CatalogModel"]
      )[];
      /** Next Cursor */
      next_cursor: string | null;
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
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
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
      /** Settings */
      settings?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
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
    /**
     * ModelRequestMetrics
     * @description Derived from the current model contribution.
     */
    ModelRequestMetrics: {
      /** Output Tokens Per Second */
      output_tokens_per_second: number | null;
      /** Request Duration Seconds */
      request_duration_seconds: number | null;
      /** Time To First Chunk Seconds */
      time_to_first_chunk_seconds: number | null;
    };
    /** ModelSummary */
    ModelSummary: {
      /** Capabilities */
      capabilities: string[];
      /** Context Window Tokens */
      context_window_tokens: number | null;
      /** Name */
      name: string;
      /**
       * Omitted Sections
       * @default [
       *       "characteristics",
       *       "pricing"
       *     ]
       */
      omitted_sections?: string[];
      /** Pricing Available */
      pricing_available: boolean;
      ref: components["schemas"]["CatalogRef"];
      /**
       * View
       * @default compact
       * @constant
       */
      view?: "compact";
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
      /** Average Time To First Chunk Seconds */
      average_time_to_first_chunk_seconds: number | null;
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
      /** Output Tokens Per Second */
      output_tokens_per_second: number | null;
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
      /**
       * @default {
       *       "operations": [
       *         "environment.file.copy_destination",
       *         "environment.file.copy_source",
       *         "environment.file.list",
       *         "environment.file.mkdir",
       *         "environment.file.move",
       *         "environment.file.patch_text",
       *         "environment.file.query",
       *         "environment.file.read_bytes",
       *         "environment.file.read_text",
       *         "environment.file.remove",
       *         "environment.file.search_text",
       *         "environment.file.stat",
       *         "environment.file.write_bytes",
       *         "environment.file.write_text",
       *         "environment.output.read",
       *         "environment.output.release",
       *         "environment.port.inspect",
       *         "environment.port.wait",
       *         "environment.process.close_stdin",
       *         "environment.process.inspect",
       *         "environment.process.kill",
       *         "environment.process.list",
       *         "environment.process.read_output",
       *         "environment.process.release",
       *         "environment.process.signal",
       *         "environment.process.start",
       *         "environment.process.wait",
       *         "environment.process.write_stdin",
       *         "environment.shell.exec",
       *         "environment.state.export",
       *         "environment.state.restore"
       *       ]
       *     }
       */
      permission_ceiling?: components["schemas"]["EnvironmentPermissionSet-Input"];
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
    /** MountUpdate */
    MountUpdate: {
      permission_ceiling: components["schemas"]["EnvironmentPermissionSet-Input"];
    };
    /** MountView */
    MountView: {
      /** Environment Id */
      environment_id: string;
      /** Name */
      name: string;
      /**
       * @default {
       *       "operations": [
       *         "environment.file.copy_destination",
       *         "environment.file.copy_source",
       *         "environment.file.list",
       *         "environment.file.mkdir",
       *         "environment.file.move",
       *         "environment.file.patch_text",
       *         "environment.file.query",
       *         "environment.file.read_bytes",
       *         "environment.file.read_text",
       *         "environment.file.remove",
       *         "environment.file.search_text",
       *         "environment.file.stat",
       *         "environment.file.write_bytes",
       *         "environment.file.write_text",
       *         "environment.output.read",
       *         "environment.output.release",
       *         "environment.port.inspect",
       *         "environment.port.wait",
       *         "environment.process.close_stdin",
       *         "environment.process.inspect",
       *         "environment.process.kill",
       *         "environment.process.list",
       *         "environment.process.read_output",
       *         "environment.process.release",
       *         "environment.process.signal",
       *         "environment.process.start",
       *         "environment.process.wait",
       *         "environment.process.write_stdin",
       *         "environment.shell.exec",
       *         "environment.state.export",
       *         "environment.state.restore"
       *       ]
       *     }
       */
      permission_ceiling?: components["schemas"]["EnvironmentPermissionSet-Output"];
      /** Working Directory */
      working_directory: string | null;
    };
    /** NetworkInterfaceMetrics */
    NetworkInterfaceMetrics: {
      /** Identity */
      identity: string;
      /** Receive Drops */
      receive_drops?: number | null;
      /** Receive Errors */
      receive_errors?: number | null;
      /** Received Bytes */
      received_bytes?: number | null;
      /** Received Packets */
      received_packets?: number | null;
      /** Transmit Drops */
      transmit_drops?: number | null;
      /** Transmit Errors */
      transmit_errors?: number | null;
      /** Transmitted Bytes */
      transmitted_bytes?: number | null;
      /** Transmitted Packets */
      transmitted_packets?: number | null;
    };
    /** NetworkMetrics */
    NetworkMetrics: {
      /** Interfaces */
      interfaces?: {
        [key: string]: components["schemas"]["NetworkInterfaceMetrics"];
      };
      /** Truncated */
      truncated: boolean;
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
      execution?: components["schemas"]["ExecutionOverride"] | null;
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
      session?: components["schemas"]["SessionCreate"] | null;
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
    /** Observation */
    Observation: {
      /** Age Ms */
      age_ms?: number | null;
      /** Device Id */
      device_id: string;
      /** Generation */
      generation?: number | null;
      /** Last Sample At */
      last_sample_at?: string | null;
      /** Received At */
      received_at?: string | null;
      sample?: components["schemas"]["MetricsSample"] | null;
      /** Sample Interval Ms */
      sample_interval_ms?: number | null;
      status: components["schemas"]["Status"];
      system_info?: components["schemas"]["SystemInfo"] | null;
    };
    /** Occurrence */
    Occurrence: {
      /** Completed At */
      completed_at?: string | null;
      /** Completion Run Id */
      completion_run_id?: string | null;
      /** Covered From At */
      covered_from_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /**
       * Deadline At
       * Format: date-time
       */
      deadline_at: string;
      /** Dispatch Attempts */
      dispatch_attempts: number;
      /** Entry Id */
      entry_id: string | null;
      execution?: components["schemas"]["OccurrenceExecution"] | null;
      /** Execution Outcome */
      execution_outcome?: ("succeeded" | "failed" | "cancelled") | null;
      /** Finished At */
      finished_at: string | null;
      /** Id */
      id: string;
      /** Last Error */
      last_error: string | null;
      /** Next Attempt At */
      next_attempt_at: string | null;
      /** Principal Id */
      principal_id: string;
      /** Reason */
      reason: string | null;
      /** Requested By Id */
      requested_by_id: string | null;
      /** Schedule Id */
      schedule_id: string;
      /** Schedule Version */
      schedule_version: number;
      /** Scheduled At */
      scheduled_at: string | null;
      /** Session Id */
      session_id: string;
      snapshot: components["schemas"]["OccurrenceSnapshot"];
      status: components["schemas"]["OccurrenceStatus"];
      /** Thread Id */
      thread_id: string | null;
      trigger: components["schemas"]["ScheduleTrigger"];
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /**
     * OccurrenceExecution
     * @description Current execution observations alongside the durable occurrence completion facts.
     */
    OccurrenceExecution: {
      /** Has Unfinished Work */
      has_unfinished_work: boolean;
      initial_entry_status: components["schemas"]["EntryStatus"] | null;
      initial_run: components["schemas"]["InitialRun"] | null;
      /**
       * Observed At
       * Format: date-time
       */
      observed_at: string;
    };
    /** OccurrencePage */
    OccurrencePage: {
      /** Items */
      items: components["schemas"]["OccurrenceSummary"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** OccurrenceSnapshot */
    OccurrenceSnapshot: {
      invocation: components["schemas"]["ScheduleInvocation-Output"];
      /** Max Lateness Seconds */
      max_lateness_seconds: number;
      /** Name */
      name: string;
      schedule: components["schemas"]["ScheduleRule"];
      /** Timezone Data Version */
      timezone_data_version: string;
      /** Utc Offset Seconds */
      utc_offset_seconds: number | null;
    };
    /** @enum {string} */
    OccurrenceStatus:
      | "pending"
      | "submitted"
      | "completed"
      | "skipped"
      | "failed"
      | "cancelled";
    /** OccurrenceSummary */
    OccurrenceSummary: {
      /** Completed At */
      completed_at?: string | null;
      /** Completion Run Id */
      completion_run_id?: string | null;
      /** Covered From At */
      covered_from_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /**
       * Deadline At
       * Format: date-time
       */
      deadline_at: string;
      /** Dispatch Attempts */
      dispatch_attempts: number;
      /** Entry Id */
      entry_id: string | null;
      execution?: components["schemas"]["OccurrenceExecution"] | null;
      /** Execution Outcome */
      execution_outcome?: ("succeeded" | "failed" | "cancelled") | null;
      /** Finished At */
      finished_at: string | null;
      /** Id */
      id: string;
      /** Last Error */
      last_error: string | null;
      /** Next Attempt At */
      next_attempt_at: string | null;
      /** Reason */
      reason: string | null;
      /** Schedule Id */
      schedule_id: string;
      /** Schedule Version */
      schedule_version: number;
      /** Scheduled At */
      scheduled_at: string | null;
      /** Session Id */
      session_id: string;
      status: components["schemas"]["OccurrenceStatus"];
      /** Thread Id */
      thread_id: string | null;
      trigger: components["schemas"]["ScheduleTrigger"];
      /**
       * Updated At
       * Format: date-time
       */
      updated_at: string;
      /** Version */
      version: number;
    };
    /** OnceSchedule */
    OnceSchedule: {
      /**
       * Run At
       * Format: date-time
       */
      run_at: string;
      /**
       * Type
       * @constant
       */
      type: "once";
    };
    /** OpenView */
    OpenView: {
      /** Mount */
      mount: string;
      /** Reason */
      reason: string;
      /** Session Id */
      session_id: string;
    };
    /**
     * OperatingSystem
     * @enum {string}
     */
    OperatingSystem: "linux" | "macos" | "windows";
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
    /** OrganizationPolicy */
    OrganizationPolicy: {
      /**
       * Check Interval Seconds
       * @default 10
       */
      check_interval_seconds?: number;
      /**
       * Cooldown Seconds
       * @default 300
       */
      cooldown_seconds?: number;
      /**
       * Enabled
       * @default false
       */
      enabled?: boolean;
      /** Model */
      model?: string | null;
      /** Trigger */
      trigger?:
        | components["schemas"]["AnyChangeTrigger"]
        | components["schemas"]["ChangeCountTrigger"];
    };
    /** OrganizationUpdate */
    OrganizationUpdate: {
      /** Name */
      name?: string | null;
    };
    /** OutputPreview */
    OutputPreview: {
      /** Observation Closed */
      observation_closed: boolean;
      /** Produced Bytes */
      produced_bytes: number | null;
      /** Text */
      text: string;
      /** Truncated */
      truncated: boolean;
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
    /** Panel */
    Panel: {
      /**
       * Description
       * @default
       */
      description?: string;
      /** Id */
      id: string;
      /**
       * Legend
       * @default true
       */
      legend?: boolean;
      position: components["schemas"]["Position"];
      /** Queries */
      queries: components["schemas"]["PanelQuery"][];
      /** Title */
      title: string;
      /**
       * Unit
       * @default auto
       * @enum {string}
       */
      unit?:
        | "auto"
        | "ratio"
        | "bytes"
        | "bytes_per_second"
        | "operations_per_second"
        | "count"
        | "seconds";
      /**
       * Visualization
       * @default timeseries
       * @enum {string}
       */
      visualization?: "timeseries" | "stat";
    };
    PanelQuery:
      | components["schemas"]["TypedQuery"]
      | components["schemas"]["PromQLQuery"];
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
      /** Desktop Control Id */
      desktop_control_id?: string | null;
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
    /** @enum {string} */
    PlatformGroup:
      | "agents"
      | "models"
      | "connections"
      | "skills"
      | "environment_templates"
      | "providers"
      | "execution"
      | "schedules";
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
    /** Position */
    Position: {
      /** H */
      h: number;
      /** W */
      w: number;
      /** X */
      x: number;
      /** Y */
      y: number;
    };
    /** @enum {string} */
    Preset: "execution" | "recovery" | "answer";
    /** @enum {string} */
    PresetKind: "composer" | "finding" | "memory_organizer";
    /** Pressure */
    Pressure: {
      full?: components["schemas"]["PressureLevel"] | null;
      some?: components["schemas"]["PressureLevel"] | null;
    };
    /** PressureLevel */
    PressureLevel: {
      /** Avg10 Percent */
      avg10_percent: number;
      /** Avg300 Percent */
      avg300_percent: number;
      /** Avg60 Percent */
      avg60_percent: number;
      /** Total Usec */
      total_usec: number;
    };
    /** Pressures */
    Pressures: {
      cpu?: components["schemas"]["Pressure"] | null;
      io?: components["schemas"]["Pressure"] | null;
      memory?: components["schemas"]["Pressure"] | null;
    };
    /** Preview */
    Preview: {
      /** Text */
      text: string;
      /** Truncated */
      truncated: boolean;
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
    /** ProcessStatusProjection */
    ProcessStatusProjection: {
      /** Cleanup */
      cleanup: string | null;
      /** Exit Code */
      exit_code: number | null;
      /** Phase */
      phase: string;
      /** Signal */
      signal: string | null;
      /** Termination Reason */
      termination_reason: string | null;
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
    /** PromQLQuery */
    PromQLQuery: {
      /** Expression */
      expression: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "promql";
      /** Source Id */
      source_id: string;
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
    /** ProviderAuthorizationRequest */
    ProviderAuthorizationRequest: {
      /**
       * New Registration
       * @default false
       */
      new_registration?: boolean;
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
    /**
     * ProviderModel
     * @description An upstream choice; wire names retain the original account-discovery contract.
     */
    ProviderModel: {
      characteristics?:
        components["schemas"]["HarnessModelCharacteristics-Output"] | null;
      /** Display Name */
      display_name: string;
      /** Slug */
      slug: string;
    };
    /** ProviderPage */
    ProviderPage: {
      /** Items */
      items: components["schemas"]["Provider"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** ProviderReference */
    ProviderReference: {
      /** Id */
      id: string;
      /** Name */
      name: string;
      /** Status */
      status: string;
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
      /** Oauth Scheme */
      oauth_scheme?: string | null;
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
      /**
       * Supports Model Discovery
       * @default false
       */
      supports_model_discovery?: boolean;
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
    /** Query */
    Query: {
      /** Endpoint */
      endpoint: string;
      /** Scope Labels */
      scope_labels?: {
        [key: string]: string;
      };
    };
    /** QueryData */
    QueryData: {
      /** Result */
      result: components["schemas"]["JsonValue"][];
      /**
       * Resulttype
       * @enum {string}
       */
      resultType: "matrix" | "vector" | "scalar" | "string";
    };
    /** QueryResult */
    QueryResult: {
      data: components["schemas"]["QueryData"];
      /** Infos */
      infos?: string[];
      /**
       * Status
       * @constant
       */
      status: "success";
      /** Warnings */
      warnings?: string[];
    };
    /** RangeQuery */
    RangeQuery: {
      /** End */
      end: number;
      /** Expression */
      expression: string;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "range";
      /** Start */
      start: number;
      /** Step */
      step: number;
    };
    /** @enum {string} */
    ReadView: "detailed" | "compact";
    /** Request */
    Request: {
      /** Mount */
      mount: string;
      /** Reason */
      reason: string;
    };
    /**
     * RequestContext
     * @description Explicit business headers, frozen in accepted Run options and kept out of model projections.
     */
    RequestContext: {
      /** Headers */
      headers?: {
        [key: string]: string;
      };
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
    /** RevisionSummary */
    RevisionSummary: {
      /** Agent Id */
      agent_id: string;
      config: components["schemas"]["ConfigSummary"];
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Digest */
      digest: string;
      /** Id */
      id: string;
      /** Is Default */
      is_default: boolean;
      note: components["schemas"]["Preview"];
      /** Number */
      number: number;
      /**
       * Omitted Sections
       * @default [
       *       "config",
       *       "note"
       *     ]
       */
      omitted_sections?: string[];
      /**
       * View
       * @default compact
       * @constant
       */
      view?: "compact";
    };
    /** RevisionSummaryPage */
    RevisionSummaryPage: {
      /** Items */
      items: components["schemas"]["RevisionSummary"][];
      /** Next Cursor */
      next_cursor: string | null;
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
     * RunAgent
     * @description The agent a run executes: its frozen revision, and that revision's configuration with the run's override
     *     applied. Models, connections and templates the configuration names stay live.
     */
    RunAgent: {
      config: components["schemas"]["AgentConfig-Output"];
      revision: components["schemas"]["AgentRevision"];
    };
    /** RunAgentSummary */
    RunAgentSummary: {
      config: components["schemas"]["ConfigSummary"];
      /** Connection Ids */
      connection_ids: string[];
      /**
       * Omitted Sections
       * @default [
       *       "original",
       *       "effective",
       *       "overrides"
       *     ]
       */
      omitted_sections?: string[];
      /** Overridden Paths */
      overridden_paths: string[];
      /** References Truncated */
      references_truncated: boolean;
      /** Revision Id */
      revision_id: string;
      /** Skill Ids */
      skill_ids: string[];
      /** Subagent Ids */
      subagent_ids: string[];
      /**
       * View
       * @default compact
       * @constant
       */
      view?: "compact";
    };
    /**
     * RunConfiguration
     * @description An accepted snapshot, independent of Agent definitions and Capability configuration.
     *
     *     Consumers explicitly opt into namespaced extensions and own their validation.
     *     Extension lookups return detached values, not mutable shared state.
     */
    "RunConfiguration-Input": {
      /**
       * Allowed Hosts
       * @description Allowed normalized hostnames/IP literals or regex:<Python pattern> rules matched against the entire normalized hostname. Null is unrestricted; an empty array denies all.
       */
      allowed_hosts?: string[] | null;
      /** Extensions */
      extensions?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /**
     * RunConfiguration
     * @description An accepted snapshot, independent of Agent definitions and Capability configuration.
     *
     *     Consumers explicitly opt into namespaced extensions and own their validation.
     *     Extension lookups return detached values, not mutable shared state.
     */
    "RunConfiguration-Output": {
      /**
       * Allowed Hosts
       * @description Allowed normalized hostnames/IP literals or regex:<Python pattern> rules matched against the entire normalized hostname. Null is unrestricted; an empty array denies all.
       */
      allowed_hosts?: string[] | null;
      /** Extensions */
      extensions?: {
        [key: string]: components["schemas"]["JsonValue"];
      };
    };
    /**
     * RunContent
     * @description One immutable saved display value, possibly truncated, read under the owning run's authority.
     */
    RunContent: {
      /** Id */
      id: string;
      /**
       * Media Type
       * @enum {string}
       */
      media_type: "text/plain" | "application/json";
      /**
       * Truncated
       * @default false
       */
      truncated?: boolean;
      value: components["schemas"]["JsonValue"];
    };
    /** RunInputPreview */
    RunInputPreview: {
      /**
       * Text
       * @description At most 200 Unicode code points of input text, in content order.
       */
      text: string;
      /** Truncated */
      truncated: boolean;
    };
    /**
     * RunItems
     * @description Items of a run's committed display, in ordinal order, with the run they describe. Ordinals are dense from 1,
     *     so the first item's ordinal tells whether earlier ones exist.
     */
    RunItems: {
      /** Complete */
      complete: boolean;
      /** Items */
      items: components["schemas"]["Item"][];
      run: components["schemas"]["RunView"];
    };
    /** RunLabels */
    RunLabels: {
      /** Labels */
      labels: {
        [key: string]: string;
      };
    };
    /**
     * RunListItem
     * @description Run discovery metadata. Full input, results and execution settings belong to the Run detail read.
     */
    RunListItem: {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Attempts */
      attempts: number;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Attempt Id */
      current_attempt_id: string | null;
      /** Id */
      id: string;
      input_preview: components["schemas"]["RunInputPreview"];
      lineage: components["schemas"]["Lineage"];
      /** Max Attempts */
      max_attempts: number;
      /** Parent Run Id */
      parent_run_id: string | null;
      /** Principal Id */
      principal_id: string;
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
      /** Version */
      version: number;
      wait_reason: components["schemas"]["WaitReason"] | null;
      /** Workspace Id */
      workspace_id: string;
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
      configuration?: components["schemas"]["RunConfiguration-Input"] | null;
      context?: components["schemas"]["RequestContext"] | null;
      execution?: components["schemas"]["ExecutionOverride"] | null;
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
      configuration?: components["schemas"]["RunConfiguration-Output"] | null;
      context?: components["schemas"]["RequestContext"] | null;
      execution?: components["schemas"]["ExecutionOverride"] | null;
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
      items: components["schemas"]["RunListItem"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** @enum {string} */
    RunStatus:
      "accepted" | "running" | "waiting" | "completed" | "failed" | "cancelled";
    /** RunSummary */
    RunSummary: {
      /** Agent Id */
      agent_id: string;
      /** Agent Name */
      agent_name: string | null;
      /** Agent Revision Id */
      agent_revision_id: string;
      /** Attempts */
      attempts: number;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Current Attempt Id */
      current_attempt_id: string | null;
      failure: components["schemas"]["FailureSummary"] | null;
      /** Id */
      id: string;
      input: components["schemas"]["Preview"];
      /**
       * Lineage
       * @enum {string}
       */
      lineage: "root" | "continue" | "fork";
      /** Max Attempts */
      max_attempts: number;
      /**
       * Omitted Sections
       * @default [
       *       "input",
       *       "output",
       *       "resume",
       *       "pending",
       *       "options",
       *       "environment_mounts",
       *       "memory_mounts",
       *       "usage_at_seal",
       *       "labels"
       *     ]
       */
      omitted_sections?: string[];
      output: components["schemas"]["Preview"];
      /** Parent Run Id */
      parent_run_id: string | null;
      /** Principal Id */
      principal_id: string;
      /**
       * Revision Selection
       * @enum {string}
       */
      revision_selection: "pinned" | "default" | "inherited";
      schedule_origin?: components["schemas"]["ScheduleOrigin"] | null;
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
      usage: components["schemas"]["Preview"];
      /** Version */
      version: number;
      /**
       * View
       * @default compact
       * @constant
       */
      view?: "compact";
      wait_reason: components["schemas"]["WaitReason"] | null;
      /** Workspace Id */
      workspace_id: string;
    };
    /** RunUsage */
    RunUsage: {
      /** Models */
      models: components["schemas"]["ModelUsage"][];
      /** Run Id */
      run_id: string;
      /**
       * Run Number
       * @description One-based chronological position among all Runs in the Thread.
       */
      run_number: number;
      /** Thread Id */
      thread_id: string;
    };
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
      memory_mounts: (
        | components["schemas"]["MemoryMount"]
        | components["schemas"]["FrozenMemoryMount"]
      )[];
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
      schedule_origin?: components["schemas"]["ScheduleOrigin"] | null;
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
    /** Schedule */
    Schedule: {
      /** Archived At */
      archived_at: string | null;
      /**
       * Created At
       * Format: date-time
       */
      created_at: string;
      /** Created By Id */
      created_by_id: string;
      /** Description */
      description?: string | null;
      /** Id */
      id: string;
      invocation: components["schemas"]["ScheduleInvocation-Output"];
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /**
       * Max Lateness Seconds
       * @default 300
       */
      max_lateness_seconds?: number;
      /** Name */
      name: string;
      /** Pause Reason */
      pause_reason: ("manual" | "authority_invalid" | "target_invalid") | null;
      /** Principal Id */
      principal_id: string;
      schedule: components["schemas"]["ScheduleRule"];
      /** Session Id */
      session_id: string;
      state: components["schemas"]["ScheduleState"];
      /** Stream Url */
      stream_url: string;
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
     * ScheduleCommand
     * @description Commands accept no configuration or authority overrides.
     */
    ScheduleCommand: Record<string, never>;
    /** ScheduleCreate */
    ScheduleCreate: {
      /** Description */
      description?: string | null;
      invocation: components["schemas"]["ScheduleInvocation-Input"];
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /**
       * Max Lateness Seconds
       * @default 300
       */
      max_lateness_seconds?: number;
      /** Name */
      name: string;
      schedule: components["schemas"]["ScheduleRule"];
      /**
       * State
       * @default paused
       * @enum {string}
       */
      state?: "paused" | "enabled";
    };
    /** ScheduleInvocation */
    "ScheduleInvocation-Input": {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string | null;
      /**
       * Memories
       * @default []
       */
      memories?: components["schemas"]["MemoryMount"][];
      options?: components["schemas"]["RunOptions-Input"];
      payload: components["schemas"]["MessagePayload"];
    };
    /** ScheduleInvocation */
    "ScheduleInvocation-Output": {
      /** Agent Id */
      agent_id: string;
      /** Agent Revision Id */
      agent_revision_id: string | null;
      /**
       * Memories
       * @default []
       */
      memories?: components["schemas"]["MemoryMount"][];
      options?: components["schemas"]["RunOptions-Output"];
      payload: components["schemas"]["MessagePayload"];
    };
    /** ScheduleOrigin */
    ScheduleOrigin: {
      /** Occurrence Id */
      occurrence_id: string;
      /** Schedule Id */
      schedule_id: string;
    };
    /** SchedulePage */
    SchedulePage: {
      /** Items */
      items: components["schemas"]["Schedule"][];
      /** Next Cursor */
      next_cursor?: string | null;
    };
    /** SchedulePreview */
    SchedulePreview: {
      /** Items */
      items: components["schemas"]["ScheduleTime"][];
    };
    /** SchedulePreviewRequest */
    SchedulePreviewRequest: {
      /**
       * After
       * Format: date-time
       */
      after: string;
      /**
       * Count
       * @default 5
       */
      count?: number;
      schedule: components["schemas"]["ScheduleRule"];
    };
    ScheduleRule:
      | components["schemas"]["CronSchedule"]
      | components["schemas"]["OnceSchedule"];
    /** @enum {string} */
    ScheduleState: "paused" | "enabled" | "completed" | "archived";
    /** ScheduleStatus */
    ScheduleStatus: {
      busy_occurrences: components["schemas"]["BusyOccurrencePage"];
      /** Has Unfinished Work */
      has_unfinished_work: boolean;
      /** Last Occurrence Id */
      last_occurrence_id: string | null;
      /** Next Fire At */
      next_fire_at: string | null;
      /**
       * Observed At
       * Format: date-time
       */
      observed_at: string;
      /** Pending Occurrence Id */
      pending_occurrence_id: string | null;
      /** Schedule Id */
      schedule_id: string;
      /** Session Id */
      session_id: string;
    };
    /** ScheduleTime */
    ScheduleTime: {
      /**
       * Local
       * Format: date-time
       */
      local: string;
      /**
       * Utc
       * Format: date-time
       */
      utc: string;
    };
    /** @enum {string} */
    ScheduleTrigger: "scheduled" | "manual";
    /** ScheduleUpdate */
    ScheduleUpdate: {
      /** Description */
      description?: string | null;
      invocation?: components["schemas"]["ScheduleInvocation-Input"] | null;
      /** Labels */
      labels?: {
        [key: string]: string;
      } | null;
      /** Max Lateness Seconds */
      max_lateness_seconds?: number | null;
      /** Name */
      name?: string | null;
      schedule?: components["schemas"]["ScheduleRule"] | null;
    };
    /** SelectedTrace */
    SelectedTrace: {
      /** Run Id */
      run_id: string;
      /** Trace Id */
      trace_id: string;
    };
    /** Selection */
    Selection: {
      /** Metrics Data Source Id */
      metrics_data_source_id: string | null;
      /** Metrics Labels */
      metrics_labels?: {
        [key: string]: string;
      };
    };
    /** Series */
    Series: {
      /** Labels */
      labels?: {
        [key: string]: string;
      };
      /** Metric */
      metric: string;
      /** Points */
      points: [number, number, number, number][];
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
      memory?: components["schemas"]["SessionMemorySettings"] | null;
      origin?: components["schemas"]["SessionOrigin"] | null;
      /** Title */
      title?: string | null;
    };
    /**
     * SessionMemorySettings
     * @description Participation and complete Space selection; policy belongs to each Space.
     */
    SessionMemorySettings: {
      /**
       * Enabled
       * @default false
       */
      enabled?: boolean;
      /**
       * Spaces
       * @default []
       */
      spaces?: components["schemas"]["SpaceBinding"][];
    };
    /**
     * SessionOrigin
     * @description Caller-supplied display context, not execution provenance or an authorization boundary.
     */
    SessionOrigin: {
      /** Application */
      application: string;
      /** External Id */
      external_id?: string | null;
      /** Name */
      name?: string | null;
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
      labels?: {
        [key: string]: string;
      } | null;
      memory?: components["schemas"]["SessionMemorySettings"] | null;
      origin?: components["schemas"]["SessionOrigin"] | null;
      /** Title */
      title?: string | null;
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
      memory?: components["schemas"]["SessionMemorySettings"];
      origin?: components["schemas"]["SessionOrigin"] | null;
      preview?: components["schemas"]["SessionPreview"] | null;
      /**
       * Purpose
       * @default conversation
       * @enum {string}
       */
      purpose?: "conversation" | "memory_organization";
      /**
       * Run Count
       * @default 0
       */
      run_count?: number;
      /** Schedule Id */
      schedule_id?: string | null;
      /** Title */
      title?: string | null;
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
    /** @enum {string} */
    Severity: "critical" | "warning" | "suggestion";
    /**
     * SharedSkillSync
     * @description Publish mutable conventional names; the last completed publication wins.
     */
    SharedSkillSync: {
      /**
       * Cleanup
       * @default false
       */
      cleanup?: boolean;
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      mode: "shared";
      /**
       * Overwrite
       * @default true
       */
      overwrite?: boolean;
    };
    /** ShellPreview */
    ShellPreview: {
      status: components["schemas"]["ProcessStatusProjection"];
      stderr: components["schemas"]["OutputPreview"];
      stdout: components["schemas"]["OutputPreview"];
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
     * SpaceBinding
     * @description A named Space selected by the App or inherited from Agent defaults.
     */
    SpaceBinding: {
      /** @default write */
      access?: components["schemas"]["MemoryAccess"];
      /** Name */
      name: string;
      /** Space Id */
      space_id: string;
    };
    /** SpaceOrganizationStatus */
    SpaceOrganizationStatus: {
      /** Active Run Id */
      active_run_id?: string | null;
      /** Due At */
      due_at?: string | null;
      failure?: components["schemas"]["Failure"] | null;
      /**
       * History Gap
       * @default false
       */
      history_gap?: boolean;
      /** Last Run Id */
      last_run_id?: string | null;
      /**
       * Pending
       * @default false
       */
      pending?: boolean;
      /**
       * Processed Seq
       * @default 0
       */
      processed_seq?: number;
      /** Session Id */
      session_id?: string | null;
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
     * SpanContentPage
     * @description A matching span's selected content, or no items when the trace has no such span.
     */
    SpanContentPage: {
      /** Items */
      items: components["schemas"]["TextRead"][];
      /** Next Cursor */
      next_cursor: string | null;
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
    /**
     * SpanOverview
     * @description Exact timeline and cost fields; payloads are read from the individual span.
     */
    SpanOverview: {
      /** Cost Usd */
      cost_usd: string | null;
      /** Ended At */
      ended_at: string | null;
      /** Id */
      id: string;
      /** Kind */
      kind: string;
      /** Level */
      level: string | null;
      /** Model */
      model: string | null;
      /** Name */
      name: string;
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
      status: "ok" | "error";
      /** Trace Id */
      trace_id: string;
      /**
       * View
       * @default overview
       * @constant
       */
      view?: "overview";
    };
    /** SpanPage */
    SpanPage: {
      /** Items */
      items: components["schemas"]["Span"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SpanReadPage */
    SpanReadPage: {
      /** Items */
      items: (
        | components["schemas"]["SpanSummary"]
        | components["schemas"]["SpanOverview"]
        | components["schemas"]["Span"]
      )[];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /** SpanSummary */
    SpanSummary: {
      /** Cost Usd */
      cost_usd: string | null;
      /** Duration Ms */
      duration_ms: number | null;
      /** Ended At */
      ended_at: string | null;
      error: components["schemas"]["Preview"];
      /** Id */
      id: string;
      /** Kind */
      kind: string;
      model: components["schemas"]["Preview"];
      name: components["schemas"]["Preview"];
      /**
       * Omitted Sections
       * @default [
       *       "input",
       *       "output",
       *       "attributes",
       *       "resource_attributes",
       *       "events",
       *       "links",
       *       "usage"
       *     ]
       */
      omitted_sections?: string[];
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
      status: "ok" | "error";
      /** Trace Id */
      trace_id: string;
      usage: components["schemas"]["Preview"];
      /**
       * View
       * @default compact
       * @constant
       */
      view?: "compact";
    };
    /** @enum {string} */
    State:
      | "reload_requested"
      | "not_ready"
      | "unsupported"
      | "disabled"
      | "discovery_failed"
      | "changed";
    /** @enum {string} */
    Status:
      | "waiting"
      | "ready"
      | "stale"
      | "offline"
      | "disabled"
      | "unsupported"
      | "not_ready"
      | "capacity";
    /** @enum {string} */
    Steering: "boundary" | "immediate";
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
     * @description Absent fields match every event; a specified identity must exist and match.
     */
    SubscriptionFilter: {
      /** Agent Id */
      agent_id?: string | null;
      /** Schedule Id */
      schedule_id?: string | null;
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
    /** SystemInfo */
    SystemInfo: {
      /** Architecture */
      architecture?: string | null;
      /** Cpu Model */
      cpu_model?: string | null;
      /** Kernel Version */
      kernel_version?: string | null;
      os: components["schemas"]["OperatingSystem"];
      /** Os Version */
      os_version?: string | null;
    };
    /** SystemMetrics */
    SystemMetrics: {
      /** Commit Limit Bytes */
      commit_limit_bytes?: number | null;
      /** Commit Total Bytes */
      commit_total_bytes?: number | null;
      /** Cpu Busy Percent */
      cpu_busy_percent?: number | null;
      cpu_time_seconds?: components["schemas"]["CpuTimeSeconds"] | null;
      /** Handle Count */
      handle_count?: number | null;
      /** Logical Cpu Count */
      logical_cpu_count?: number | null;
      /** Memory Available Bytes */
      memory_available_bytes?: number | null;
      /** Memory Cache Bytes */
      memory_cache_bytes?: number | null;
      /** Memory Compressed Bytes */
      memory_compressed_bytes?: number | null;
      /** Memory Total Bytes */
      memory_total_bytes?: number | null;
      /** Memory Wired Bytes */
      memory_wired_bytes?: number | null;
      pressure?: components["schemas"]["Pressures"] | null;
      /** Process Count */
      process_count?: number | null;
      /** Swap Total Bytes */
      swap_total_bytes?: number | null;
      /** Swap Used Bytes */
      swap_used_bytes?: number | null;
      /** Thread Count */
      thread_count?: number | null;
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
    /**
     * TextRead
     * @description A fragment of a versioned value, not a complete JSON object or writable configuration.
     */
    TextRead: {
      /** Has More */
      has_more: boolean;
      /** Match Offset */
      match_offset?: number | null;
      /**
       * Media Type
       * @enum {string}
       */
      media_type: "text/plain" | "application/json";
      /** Next Offset */
      next_offset: number | null;
      /** Offset */
      offset: number;
      /** Query */
      query?: string | null;
      /**
       * Representation
       * @default fragment
       * @constant
       */
      representation?: "fragment";
      /** Source */
      source: string;
      /**
       * Source Truncated
       * @default false
       */
      source_truncated?: boolean;
      /** Text */
      text: string;
      /** Total Chars */
      total_chars: number;
      /** Version */
      version: string;
    };
    /** ThreadPage */
    ThreadPage: {
      /** Items */
      items: components["schemas"]["ThreadView"][];
      /** Next Cursor */
      next_cursor: string | null;
    };
    /**
     * ThreadRunPage
     * @description Newest first; first_number is the first item's 1-based chronological position in the thread.
     */
    ThreadRunPage: {
      /** First Number */
      first_number: number;
      /** Items */
      items: components["schemas"]["RunListItem"][];
      /** Next Cursor */
      next_cursor: string | null;
      /** Previous Cursor */
      previous_cursor: string | null;
      /** Total */
      total: number;
    };
    /** ThreadUpdate */
    ThreadUpdate: {
      execution?: components["schemas"]["ExecutionOverride"] | null;
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
      execution?: components["schemas"]["ExecutionOverride"] | null;
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
      /**
       * Purpose
       * @default conversation
       * @enum {string}
       */
      purpose?: "conversation" | "memory_organization";
      /** Schedule Occurrence Id */
      schedule_occurrence_id?: string | null;
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
      /**
       * Description
       * @default
       */
      description?: string;
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
    /** ToolExecutionOverride */
    ToolExecutionOverride: {
      /** Foreground Wait Seconds */
      foreground_wait_seconds?: number | null;
      /** Mode */
      mode?: ("inline" | "auto") | null;
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
    /**
     * ToolSearch
     * @description Presentation of already-selected sources, composed by Harness once per agent.
     */
    ToolSearch: {
      config?: components["schemas"]["ToolSearchConfig"];
      /** Groups */
      groups?: {
        [key: string]: components["schemas"]["ToolSearchGroup"];
      };
    };
    /**
     * ProxyToolSearchConfig
     * @description Names and bounded discovery settings for one Agent's proxy surface.
     */
    ToolSearchConfig: {
      /**
       * Call Name
       * @default call_tool
       */
      call_name?: string;
      /**
       * Describe Name
       * @default describe_tool
       */
      describe_name?: string;
      /**
       * Groups Name
       * @default list_tool_groups
       */
      groups_name?: string;
      /**
       * List Name
       * @default list_tools
       */
      list_name?: string;
      /**
       * Max Results
       * @default 10
       */
      max_results?: number;
      /**
       * Max Search Bytes
       * @default 32768
       */
      max_search_bytes?: number;
      /**
       * Mode
       * @default proxy
       * @constant
       */
      mode?: "proxy";
      /**
       * Search Name
       * @default discover_tools
       */
      search_name?: string;
    };
    /** ToolSearchGroup */
    ToolSearchGroup: {
      /**
       * Capabilities
       * @default []
       */
      capabilities?: ("environment" | "web")[];
      /**
       * Connections
       * @default []
       */
      connections?: string[];
      /** Description */
      description: string;
      /**
       * Platform
       * @default false
       */
      platform?: boolean;
      /**
       * Plugins
       * @default []
       */
      plugins?: string[];
    };
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
      /**
       * Description
       * @default
       */
      description?: string;
      /** Display Name */
      display_name: string;
      key: components["schemas"]["ToolsetKey"];
      /**
       * Platform
       * @default false
       */
      platform?: boolean;
      /** Tools */
      tools: components["schemas"]["ToolDefinition"][];
    };
    ToolsetKey:
      | ("files" | "shell" | "computer" | "web" | "memory" | "assets")
      | components["schemas"]["PlatformGroup"];
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
    /** TraceSearchItem */
    TraceSearchItem: {
      /** Cost Usd */
      cost_usd: string | null;
      /** Duration Seconds */
      duration_seconds: string | null;
      root: components["schemas"]["Span"];
    };
    /** TraceSearchPage */
    TraceSearchPage: {
      /**
       * Collected At
       * Format: date-time
       */
      collected_at: string;
      /** Items */
      items: components["schemas"]["TraceSearchItem"][];
      /** Next Cursor */
      next_cursor: string | null;
      /** Total */
      total: number;
    };
    /** @enum {string} */
    Trigger:
      | "input"
      | "queued"
      | "resume"
      | "child_result"
      | "tool_result"
      | "spawned";
    /** TypedQuery */
    TypedQuery: {
      /**
       * History
       * @default configured
       * @enum {string}
       */
      history?: "configured" | "temporary";
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "metric";
      /** Metric */
      metric:
        | (
            | "system_cpu_busy_ratio"
            | "system_memory_used_bytes"
            | "cgroup_memory_current_bytes"
            | "system_memory_total_bytes"
            | "system_memory_available_bytes"
            | "system_memory_cache_bytes"
            | "system_memory_wired_bytes"
            | "system_memory_compressed_bytes"
            | "system_swap_used_bytes"
            | "system_swap_total_bytes"
            | "system_commit_total_bytes"
            | "system_commit_limit_bytes"
            | "system_processes"
            | "system_threads"
            | "system_handles"
            | "cgroup_pids_current"
            | "tcp_connections"
            | "udp_sockets"
            | "daemon_sessions"
            | "daemon_processes"
            | "daemon_connections"
            | "daemon_transfers"
            | "daemon_retained_output_bytes"
          )
        | (
            | "network_received_bytes_per_second"
            | "network_transmitted_bytes_per_second"
            | "disk_read_bytes_per_second"
            | "disk_written_bytes_per_second"
            | "disk_read_operations_per_second"
            | "disk_write_operations_per_second"
            | "cgroup_io_read_bytes_per_second"
            | "cgroup_io_written_bytes_per_second"
            | "cgroup_io_read_operations_per_second"
            | "cgroup_io_write_operations_per_second"
          );
    };
    /** UnlimitedCpuLimit */
    UnlimitedCpuLimit: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "unlimited";
    };
    /** UnlimitedMemoryLimit */
    UnlimitedMemoryLimit: {
      /**
       * @description discriminator enum property added by openapi-typescript
       * @enum {string}
       */
      kind: "unlimited";
    };
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
    /**
     * UrlInputSupport
     * @description URL subtypes consumed natively by the selected transport.
     */
    "UrlInputSupport-Input": {
      /** Video */
      video?: components["schemas"]["VideoUrlType"][];
    };
    /**
     * UrlInputSupport
     * @description URL subtypes consumed natively by the selected transport.
     */
    "UrlInputSupport-Output": {
      /** Video */
      video?: string[];
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
     *     The request count is tracked by Pydantic AI, and the request limit is checked at the start of each step of the
     *     agent loop. Token counts are provided in responses from the model, and the token limits are checked after each response.
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
     *     The request count is tracked by Pydantic AI, and the request limit is checked at the start of each step of the
     *     agent loop. Token counts are provided in responses from the model, and the token limits are checked after each response.
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
      /** Runs */
      runs: components["schemas"]["RunUsage"][];
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
    /** VercelOption */
    VercelOption: {
      /** Id */
      id: string;
      /** Name */
      name: string;
    };
    /** VercelOptions */
    VercelOptions: {
      /** Cursor */
      cursor?: number | null;
      /** Items */
      items: components["schemas"]["VercelOption"][];
    };
    /** VercelOptionsRequest */
    VercelOptionsRequest: {
      /**
       * Api Key
       * Format: password
       */
      api_key: string;
      /** Cursor */
      cursor?: number | null;
      /** Team Id */
      team_id?: string | null;
    };
    /**
     * VideoInputPolicy
     * @description Base64-after byte budget for both one video and all inline videos in a request.
     */
    VideoInputPolicy: {
      /**
       * Max Video Bytes
       * @description Maximum Base64-encoded bytes per video and in aggregate per model request.
       * @default 10485760
       */
      max_video_bytes?: number;
    };
    /**
     * VideoUrlType
     * @enum {string}
     */
    VideoUrlType: "youtube";
    /** View */
    View: {
      /**
       * Cause
       * @enum {string}
       */
      cause: "agent" | "user";
      /** Controller Id */
      controller_id: string | null;
      /**
       * Deadline
       * Format: date-time
       */
      deadline: string;
      /** Environment Id */
      environment_id: string;
      /** Epoch */
      epoch: number;
      /** Failure */
      failure: string | null;
      /** Id */
      id: string;
      /** Lease Expires At */
      lease_expires_at: string | null;
      /** Mount */
      mount: string;
      /** Outcome */
      outcome: {
        [key: string]: unknown;
      } | null;
      /** Reason */
      reason: string;
      /** Run Id */
      run_id: string;
      /** State */
      state: string;
      /** Tool Call Id */
      tool_call_id: string | null;
    };
    /** @enum {string} */
    WaitReason: "approval" | "call" | "multiple" | "desktop_control";
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
  prepare_composer: {
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
  list_agents: {
    parameters: {
      query?: {
        label?: string[] | null;
        q?: string | null;
        archived?: boolean | null;
        source?: components["schemas"]["AgentSource"] | null;
        preset_kind?: components["schemas"]["PresetKind"] | null;
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
  create_agent: {
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
  validate_agent: {
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
  get_agent: {
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
  update_agent: {
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
  archive_agent: {
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
  get_agent_avatar: {
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
  put_agent_avatar: {
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
  delete_agent_avatar: {
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
  duplicate_agent: {
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
  list_agent_revisions: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        limit?: number | null;
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
          "application/json":
            | components["schemas"]["AgentRevisionPage"]
            | components["schemas"]["RevisionSummaryPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_agent_revision: {
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
  get_agent_revision: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        section?: components["schemas"]["ConfigSection"] | null;
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
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
          "application/json":
            | components["schemas"]["AgentRevision"]
            | components["schemas"]["RevisionSummary"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  compare_agent_revisions: {
    parameters: {
      query: {
        base_revision_id: string;
        section?:
          | "all"
          | "model"
          | "model_settings"
          | "model_characteristics"
          | "instructions"
          | "toolsets"
          | "skills"
          | "skill_sync"
          | "connection_tools"
          | "tool_search"
          | "builtin_skills"
          | "client_tools"
          | "user_questions"
          | "subagent_mode"
          | "subagents"
          | "reviewer"
          | "media_understanding"
          | "plugins"
          | "output_spec"
          | "retries"
          | "default_environment_template_id"
          | "lazy_environment"
          | "memory_mounts"
          | "memory";
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
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
          "application/json": components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  set_default_agent_revision: {
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
  unarchive_agent: {
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
  list_assets: {
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
  create_asset: {
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
  get_asset: {
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
  retire_asset: {
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
  read_asset_content: {
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
  bootstrap_administrator: {
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
  auth_configuration: {
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
  confirm_email_change: {
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
  password_login: {
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
  logout: {
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
  request_password_reset: {
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
  confirm_password_reset: {
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
  session_profile: {
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
  list_connections: {
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
  create_connection: {
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
  complete_authorization: {
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
  get_redirect_uri: {
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
  get_connection: {
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
  update_connection: {
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
  authorize_connection: {
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
  revoke_connection: {
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
  test_connection: {
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
  list_connection_tools: {
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
  define_connection_tools: {
    parameters: {
      query: {
        /** @description Exact names; repeat name to select up to five definitions. */
        name: string[];
        offset?: number | null;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
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
          "application/json":
            | components["schemas"]["Definitions"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  index_connection_tools: {
    parameters: {
      query?: {
        /** @description Case-insensitive substring of full names and descriptions. */
        query?: string | null;
        limit?: number;
        cursor?: string | null;
      };
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
          "application/json": components["schemas"]["CatalogIndex"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_connector_providers: {
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
  create_connector_provider: {
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
  get_connector_provider: {
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
  update_connector_provider: {
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
  list_connector_apps: {
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
  get_connector_app: {
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
  list_connector_actions: {
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
  define_connector_actions: {
    parameters: {
      query: {
        /** @description Exact names; repeat name to select up to five definitions. */
        name: string[];
        offset?: number | null;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
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
          "application/json":
            | components["schemas"]["Definitions"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  index_connector_actions: {
    parameters: {
      query?: {
        /** @description Case-insensitive substring of full names and descriptions. */
        query?: string | null;
        limit?: number;
        cursor?: string | null;
      };
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
          "application/json": components["schemas"]["CatalogIndex"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_connector_provider: {
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
  complete_assistance: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        assistance_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Completion"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["View"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  control_desktop: {
    parameters: {
      query?: {
        renew?: boolean;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        assistance_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Control"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Access"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  disconnect_desktop: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        assistance_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Control"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["View"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  request_control: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        assistance_id: string;
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
          "application/json": components["schemas"]["View"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  view_desktop: {
    parameters: {
      query?: {
        renew?: boolean;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        assistance_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["Control"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Access"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_devices: {
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
          "application/json": components["schemas"]["DevicePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_device: {
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
        "application/json": components["schemas"]["DeviceCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DeviceCredential"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_device: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        device_id: string;
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
          "application/json": components["schemas"]["Device"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_device: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        device_id: string;
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
  update_device: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        device_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DeviceUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Device"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  query_device_metrics: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        device_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["EnvironmentQuery"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json":
            | components["schemas"]["QueryResult"]
            | components["schemas"]["LocalResult"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  rotate_device_credential: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        device_id: string;
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
          "application/json": components["schemas"]["DeviceCredential"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  vercel_provider_options: {
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
        "application/json": components["schemas"]["VercelOptionsRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["VercelOptions"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_environment_providers: {
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
  create_environment_provider: {
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
  get_environment_provider: {
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
  delete_environment_provider: {
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
  update_environment_provider: {
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
  environment_provider_references: {
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
          "application/json": components["schemas"]["EnvironmentProviderReferences"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_environment_provider: {
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
  list_environment_templates: {
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
  create_environment_template: {
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
  get_environment_template: {
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
  update_environment_template: {
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
  list_environments: {
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
  create_environment: {
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
  get_environment: {
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
  delete_environment: {
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
  update_environment: {
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
  check_computer: {
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
          "application/json": components["schemas"]["ComputerCheck"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_environment_creation: {
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
          "application/json": components["schemas"]["CreationProgress"] | null;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_environment_desktop_preview: {
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
          "image/jpeg": unknown;
          "image/png": unknown;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  select_metrics_source: {
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
        "application/json": components["schemas"]["Selection"];
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
  get_collector_status: {
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
          "application/json": components["schemas"]["CollectorStatus"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  query_environment_metrics: {
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
    requestBody: {
      content: {
        "application/json": components["schemas"]["EnvironmentQuery"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json":
            | components["schemas"]["QueryResult"]
            | components["schemas"]["LocalResult"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  retry_environment_creation: {
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
  stop_environment: {
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
  prepare_finding_agent: {
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
  list_finding_analyses: {
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
          "application/json": components["schemas"]["AnalysisPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  start_analysis: {
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
        "application/json": components["schemas"]["AnalysisCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Analysis"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_findings: {
    parameters: {
      query?: {
        agent_id?: string | null;
        category?: components["schemas"]["Category"] | null;
        severity?: components["schemas"]["Severity"] | null;
        assessment?: components["schemas"]["Assessment"] | null;
        closed?: boolean | null;
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
          "application/json": components["schemas"]["FindingPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_finding: {
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
        "application/json": components["schemas"]["FindingCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Finding"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_finding: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        finding_id: string;
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
          "application/json": components["schemas"]["Finding"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_finding: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        finding_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["FindingUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Finding"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  accept: {
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
  list_mcp_servers: {
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
  get_media_defaults: {
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
  replace_media_defaults: {
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
  list_memories: {
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
  create_memory: {
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
  get_memory: {
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
  delete_memory: {
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
  update_memory: {
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
  list_files: {
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
  create_file: {
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
  move_file: {
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
  read_file: {
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
  replace_file: {
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
  delete_file: {
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
  list_records: {
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
  add_record: {
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
  search_records: {
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
  update_record: {
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
  delete_record: {
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
  list_memory_revisions: {
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
  purge_history: {
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
  get_memory_revision: {
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
  restore_memory_revision: {
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
  list_memory_providers: {
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
  create_memory_provider: {
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
  get_memory_provider: {
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
  update_memory_provider: {
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
  test_memory_provider: {
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
  list_spaces: {
    parameters: {
      query?: {
        label?: string[] | null;
        enabled?: boolean | null;
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
          "application/json": components["schemas"]["MemorySpacePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_space: {
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
        "application/json": components["schemas"]["MemorySpaceCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemorySpace"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_space: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        space_id: string;
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
          "application/json": components["schemas"]["MemorySpace"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_space: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        space_id: string;
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
  update_space: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        space_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["MemorySpaceUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["MemorySpace"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_space_organization: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        space_id: string;
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
          "application/json": components["schemas"]["SpaceOrganizationStatus"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_space_organizations: {
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
        space_id: string;
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
  organize_space: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        space_id: string;
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
          "application/json": components["schemas"]["SpaceOrganizationStatus"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_space_sessions: {
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
        space_id: string;
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
          "application/json": components["schemas"]["SessionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_dashboards: {
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
          "application/json": components["schemas"]["DashboardPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_dashboard: {
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
        "application/json": components["schemas"]["DashboardCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Dashboard"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_dashboard_preset: {
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
          "application/json": components["schemas"]["DashboardConfig"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_dashboard: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        dashboard_id: string;
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
          "application/json": components["schemas"]["Dashboard"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_dashboard: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        dashboard_id: string;
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
  update_dashboard: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        dashboard_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DashboardUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Dashboard"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_metrics_data_sources: {
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
          "application/json": components["schemas"]["DataSourcePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_metrics_data_source: {
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
        "application/json": components["schemas"]["DataSourceCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DataSource"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_metrics_capabilities: {
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
          "application/json": components["schemas"]["Capabilities"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_metrics_deployment_preset: {
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
          "application/json": components["schemas"]["DeploymentPreset"] | null;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_metrics_data_source: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        source_id: string;
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
          "application/json": components["schemas"]["DataSource"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  delete_metrics_data_source: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        source_id: string;
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
  update_metrics_data_source: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        source_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["DataSourceUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["DataSource"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  query_metrics_data_source: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        source_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json":
          | components["schemas"]["InstantQuery"]
          | components["schemas"]["RangeQuery"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["QueryResult"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_model_catalog: {
    parameters: {
      query?: {
        /** @description Exact catalog channel ID, such as openrouter. */
        provider?: string | null;
        /** @description Exact, case-sensitive model ID within the catalog channel. */
        model?: string | null;
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        /** @description Case-insensitive substring of full names and descriptions. */
        query?: string | null;
        capability?: string | null;
        limit?: number | null;
        cursor?: string | null;
        section?: ("all" | "characteristics" | "pricing") | null;
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        text_limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
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
          "application/json":
            | components["schemas"]["ModelCatalog"]
            | components["schemas"]["ModelCatalogPage"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_model_providers: {
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
  create_model_provider: {
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
  get_model_provider: {
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
  update_model_provider: {
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
  model_authorization: {
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
          "application/json": components["schemas"]["AuthorizationStatus"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  disconnect_model_authorization: {
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
          "application/json": components["schemas"]["AuthorizationDisconnect"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  complete_model_authorization: {
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
    requestBody: {
      content: {
        "application/json": components["schemas"]["AuthorizationCallback"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationStatus"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  authorize_model: {
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
    requestBody: {
      content: {
        "application/json": components["schemas"]["ProviderAuthorizationRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["AuthorizationStart"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  discover_model_provider_models: {
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
          "application/json": components["schemas"]["ProviderModel"][];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  test_model_provider: {
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
  list_models: {
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
  create_model: {
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
  get_model: {
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
  update_model: {
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
  list_organizations: {
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
  get_organization: {
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
  update_organization: {
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
  list_organization_audit_events: {
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
  list_organization_grants: {
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
  create_organization_grant: {
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
  delete_organization_grant: {
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
  change_organization_grant: {
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
  get_organization_icon: {
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
  put_organization_icon: {
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
  delete_organization_icon: {
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
  list_organization_invitations: {
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
  create_organization_invitation: {
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
  resend_organization_invitation: {
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
  revoke_organization_invitation: {
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
  list_members: {
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
  list_organization_workspaces: {
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
  create_workspace: {
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
  list_provider_types: {
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
  list_runs: {
    parameters: {
      query?: {
        /** @description All search terms must match one run's retained user/assistant text */
        text_query?: string | null;
        agent_id?: string | null;
        session_id?: string | null;
        thread_id?: string | null;
        principal_id?: string | null;
        status?:
          | (
              | "accepted"
              | "running"
              | "waiting"
              | "completed"
              | "failed"
              | "cancelled"
            )
          | null;
        created_after?: string | null;
        created_before?: string | null;
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
          "application/json": components["schemas"]["RunPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_run: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        section?:
          | (
              | "input"
              | "output"
              | "failure"
              | "resume"
              | "pending"
              | "options"
              | "environment_mounts"
              | "memory_mounts"
              | "usage_at_seal"
              | "labels"
            )
          | null;
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
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
          "application/json":
            | components["schemas"]["RunView"]
            | components["schemas"]["RunSummary"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  update_run: {
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
  get_run_agent: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        section?: ("original" | "effective" | "overrides") | null;
        part?:
          | "all"
          | "model"
          | "model_settings"
          | "model_characteristics"
          | "instructions"
          | "toolsets"
          | "skills"
          | "skill_sync"
          | "connection_tools"
          | "tool_search"
          | "builtin_skills"
          | "client_tools"
          | "user_questions"
          | "subagent_mode"
          | "subagents"
          | "reviewer"
          | "media_understanding"
          | "plugins"
          | "output_spec"
          | "retries"
          | "default_environment_template_id"
          | "lazy_environment"
          | "memory_mounts"
          | "memory";
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
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
          "application/json":
            | components["schemas"]["RunAgent"]
            | components["schemas"]["RunAgentSummary"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_run_attempts: {
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
  get_run_attempt: {
    parameters: {
      query?: never;
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
          "application/json": components["schemas"]["AttemptView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_attempt_spans: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        status?: ("ok" | "error") | null;
        kind?: string | null;
        tool_name?: string | null;
        limit?: number | null;
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
          "application/json":
            | components["schemas"]["SpanPage"]
            | components["schemas"]["SpanReadPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_run_content: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        offset?: number | null;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
        content_id: string;
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
          "application/json":
            | components["schemas"]["RunContent"]
            | components["schemas"]["ContentSummary"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_assistance: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
        call_id: string;
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
          "application/json": components["schemas"]["View"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_control: {
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
          "application/json": components["schemas"]["View"] | null;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  open_control: {
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
    requestBody: {
      content: {
        "application/json": components["schemas"]["Request"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["View"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  open_view: {
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
    requestBody: {
      content: {
        "application/json": components["schemas"]["OpenView"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Access"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  fork_run: {
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
  run_image: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
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
          "image/jpeg": Binary;
          "image/png": Binary;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  interrupt_run: {
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
  list_run_items: {
    parameters: {
      query?: {
        /** @description Return the items just before this ordinal */
        before?: number | null;
        /** @description Return the items just after this ordinal */
        after?: number | null;
        limit?: number | null;
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        kind?: components["schemas"]["ItemKind"] | null;
        state?: components["schemas"]["ItemState"] | null;
        tool_name?: string | null;
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
          "application/json":
            | components["schemas"]["RunItems"]
            | components["schemas"]["ItemPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  read_run_item_content: {
    parameters: {
      query: {
        /** @description Ordinal returned with this item ID; both must match */
        ordinal: number;
        field?:
          | "all"
          | "text"
          | "arguments"
          | "result"
          | "value"
          | "result_parts"
          | "metadata"
          | "failure";
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        run_id: string;
        item_id: string;
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
          "application/json": components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_run_lineage: {
    parameters: {
      query?: {
        cursor?: string | null;
        limit?: number;
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
  resume_run: {
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
  list_schedules: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        state?: components["schemas"]["ScheduleState"] | null;
        agent_id?: string | null;
        principal_id?: string | null;
        label?: string[];
        q?: string | null;
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
          "application/json": components["schemas"]["SchedulePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_schedule: {
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
        "application/json": components["schemas"]["ScheduleCreate"];
      };
    };
    responses: {
      /** @description Successful Response */
      201: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Schedule"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  preview_schedule: {
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
        "application/json": components["schemas"]["SchedulePreviewRequest"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["SchedulePreview"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_schedule: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
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
          "application/json": components["schemas"]["Schedule"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  archive_schedule: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
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
  update_schedule: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ScheduleUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Schedule"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  dispatch_schedule: {
    parameters: {
      query?: never;
      header: {
        "Idempotency-Key": string;
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ScheduleCommand"];
      };
    };
    responses: {
      /** @description Successful Response */
      202: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Occurrence"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  enable_schedule: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ScheduleCommand"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Schedule"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_schedule_occurrences: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        status?: components["schemas"]["OccurrenceStatus"] | null;
        trigger?: components["schemas"]["ScheduleTrigger"] | null;
        created_after?: string | null;
        created_before?: string | null;
        unfinished?: boolean | null;
        include?: "execution" | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
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
          "application/json": components["schemas"]["OccurrencePage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_schedule_occurrence: {
    parameters: {
      query?: {
        include?: "execution" | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
        occurrence_id: string;
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
          "application/json": components["schemas"]["Occurrence"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  pause_schedule: {
    parameters: {
      query?: never;
      header?: {
        /** @description The resource's ETag: `"{id}:{version}"` of its current view, `"{key}:{version}"` for a model */
        "If-Match"?: string | null;
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
      };
      cookie?: never;
    };
    requestBody: {
      content: {
        "application/json": components["schemas"]["ScheduleCommand"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
        headers: {
          [name: string]: unknown;
        };
        content: {
          "application/json": components["schemas"]["Schedule"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_schedule_status: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        schedule_id: string;
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
          "application/json": components["schemas"]["ScheduleStatus"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_sessions: {
    parameters: {
      query?: {
        /** @description All search terms must match one run's retained user/assistant text */
        text_query?: string | null;
        /** @description Session/thread ID, title, origin, label value or retained conversation text */
        q?: string | null;
        application?: string | null;
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
  create_session: {
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
  get_session: {
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
  update_session: {
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
  get_message_authors: {
    parameters: {
      query: {
        entry_id: string[];
      };
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
          "application/json": components["schemas"]["MessageAuthors"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  session_stream: {
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
          "text/event-stream": unknown;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_skills: {
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
  create_skill: {
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
  validate_skill: {
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
  get_skill: {
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
  update_skill: {
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
  archive_skill: {
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
  list_skill_revisions: {
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
  create_skill_revision: {
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
  get_skill_revision: {
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
  read_skill_archive: {
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
  read_skill_file: {
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
  set_default_skill_revision: {
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
  unarchive_skill: {
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
  list_subscriptions: {
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
  create_subscription: {
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
  get_subscription: {
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
  delete_subscription: {
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
  update_subscription: {
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
  list_deliveries: {
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
  redeliver: {
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
  list_threads: {
    parameters: {
      query?: {
        session_id?: string | null;
        occurrence_id?: string | null;
        text_query?: string | null;
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
  create_thread: {
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
  get_thread: {
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
  update_thread: {
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
  archive_thread: {
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
  list_environment_mounts: {
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
  add_environment_mount: {
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
  remove_environment_mount: {
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
  update_environment_mount: {
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
        "application/json": components["schemas"]["MountUpdate"];
      };
    };
    responses: {
      /** @description Successful Response */
      200: {
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
  list_inbox: {
    parameters: {
      query?: {
        status?: components["schemas"]["EntryStatus"][] | null;
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        kind?: ("message" | "child_result") | null;
        limit?: number | null;
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
          "application/json":
            | components["schemas"]["EntryPage"]
            | components["schemas"]["EntrySummaryPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  submit_message: {
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
  reorder_inbox: {
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
  get_inbox_entry: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        section?: ("payload" | "options" | "failure") | null;
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
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
          "application/json":
            | components["schemas"]["EntryView"]
            | components["schemas"]["EntrySummary"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  withdraw_entry: {
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
  edit_entry: {
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
  list_memory_mounts: {
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
  add_memory_mount: {
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
  remove_memory_mount: {
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
  update_memory_mount: {
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
  list_thread_runs: {
    parameters: {
      query?: {
        limit?: number;
        cursor?: string | null;
        anchor?: string | null;
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
          "application/json": components["schemas"]["ThreadRunPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  thread_stream: {
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
  list_tool_executions: {
    parameters: {
      query?: {
        run_id?: string | null;
        active?: boolean | null;
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
          "application/json": components["schemas"]["ExecutionPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_tool_execution: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        execution_id: string;
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
          "application/json": components["schemas"]["ExecutionView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  cancel_tool_execution: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        execution_id: string;
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
          "application/json": components["schemas"]["ExecutionView"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  observe_tool_execution: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        execution_id: string;
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
          "application/json": components["schemas"]["ExecutionObservation"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_tool_execution_result: {
    parameters: {
      query?: {
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        thread_id: string;
        execution_id: string;
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
          "application/json": components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_toolsets: {
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
  define_toolsets: {
    parameters: {
      query: {
        /** @description Exact names; repeat name to select up to five definitions. */
        name: string[];
        offset?: number | null;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
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
          "application/json":
            | components["schemas"]["Definitions"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  index_toolsets: {
    parameters: {
      query?: {
        /** @description Case-insensitive substring of full names and descriptions. */
        query?: string | null;
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
          "application/json": components["schemas"]["CatalogIndex"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_trace_backend: {
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
  list_traces: {
    parameters: {
      query?: {
        session_id?: string | null;
        thread_id?: string | null;
        run_id?: string | null;
        /** @description key:value, an exact root span attribute */
        attribute?: string[] | null;
        started_after?: string | null;
        started_before?: string | null;
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        status?: ("ok" | "error") | null;
        kind?: string | null;
        tool_name?: string | null;
        limit?: number | null;
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
          "application/json":
            | components["schemas"]["SpanPage"]
            | components["schemas"]["SpanReadPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  search_traces: {
    parameters: {
      query: {
        session_id?: string | null;
        thread_id?: string | null;
        run_id?: string | null;
        attribute?: string[];
        started_after: string;
        started_before: string;
        order_by?: "started" | "duration" | "cost";
        direction?: "asc" | "desc";
        min_duration_seconds?: number | string | null;
        max_duration_seconds?: number | string | null;
        min_cost_usd?: number | string | null;
        max_cost_usd?: number | string | null;
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
          "application/json": components["schemas"]["TraceSearchPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_trace: {
    parameters: {
      query?: {
        /** @description Use compact for discovery; detailed preserves the original response. */
        view?: components["schemas"]["ReadView"];
        section?:
          | (
              | "input"
              | "output"
              | "attributes"
              | "resource_attributes"
              | "events"
              | "links"
              | "usage"
            )
          | null;
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
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
          "application/json":
            | components["schemas"]["Span"]
            | components["schemas"]["SpanSummary"]
            | components["schemas"]["TextRead"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  list_trace_spans: {
    parameters: {
      query?: {
        /** @description Overview keeps exact timeline and cost fields without observation payloads. */
        view?: "detailed" | "compact" | "overview";
        status?: ("ok" | "error") | null;
        kind?: string | null;
        tool_name?: string | null;
        limit?: number | null;
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
          "application/json":
            | components["schemas"]["SpanPage"]
            | components["schemas"]["SpanReadPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_trace_span: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        trace_id: string;
        span_id: string;
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
  read_span_content: {
    parameters: {
      query?: {
        section?:
          | "all"
          | "input"
          | "output"
          | "attributes"
          | "resource_attributes"
          | "events"
          | "links"
          | "usage";
        /** @description Unicode code-point offset; continue with next_offset. */
        offset?: number;
        /** @description Maximum Unicode code points in this fragment. */
        limit?: number;
        /** @description Content version from the first read; required after offset 0. */
        version?: string | null;
        /** @description Case-sensitive literal text search. */
        query?: string | null;
      };
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        trace_id: string;
        span_id: string;
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
          "application/json": components["schemas"]["SpanContentPage"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  create_upload: {
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
  summarize_usage: {
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
  usage_agents: {
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
  context_usage: {
    parameters: {
      query?: {
        thread_id?: string | null;
        run_id?: string | null;
        record_id?: string | null;
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
          "application/json": components["schemas"]["ContextUsage"] | null;
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  usage_models: {
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
  usage_overview: {
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
  model_request_metrics: {
    parameters: {
      query?: never;
      header?: {
        /** @description The workspace ID a login session acts in; required with a login session. An API key acts in its own workspace and needs none; naming another is forbidden. */
        "X-Workspace-ID"?: string | null;
      };
      path: {
        record_id: string;
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
          "application/json": components["schemas"]["ModelRequestMetrics"];
        };
      };
      400: components["responses"]["Error"];
      default: components["responses"]["Error"];
    };
  };
  get_profile: {
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
  update_profile: {
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
  list_account_audit_events: {
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
  put_user_avatar: {
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
  delete_user_avatar: {
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
  disable_account: {
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
  list_user_keys: {
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
  create_user_key: {
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
  revoke_user_key: {
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
  list_login_sessions: {
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
  revoke_login_session: {
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
  change_password: {
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
  get_user_avatar: {
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
  list_web_providers: {
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
  create_web_provider: {
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
  get_web_provider: {
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
  update_web_provider: {
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
  test_web_provider: {
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
  list_workspaces: {
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
  get_workspace: {
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
  update_workspace: {
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
  archive_workspace: {
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
  list_workspace_audit_events: {
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
  list_workspace_grants: {
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
  create_workspace_grant: {
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
  delete_workspace_grant: {
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
  change_workspace_grant: {
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
  get_workspace_icon: {
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
  put_workspace_icon: {
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
  delete_workspace_icon: {
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
  list_workspace_invitations: {
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
  create_workspace_invitation: {
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
  resend_workspace_invitation: {
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
  revoke_workspace_invitation: {
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
  list_workspace_keys: {
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
  revoke_workspace_key: {
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
  list_service_accounts: {
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
  create_service_account: {
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
  get_service_account: {
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
  delete_service_account: {
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
  update_service_account: {
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
  list_service_account_keys: {
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
  create_service_account_key: {
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
  health: {
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
  ready: {
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
