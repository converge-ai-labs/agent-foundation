# Deployment Provider plugin example

This independently installable package registers a Web Provider and a native Pydantic AI Model Provider through one selected deployment entry point. It imports only the supported `a13n_service.provider_plugins` extension surface plus the domain types its implementations return.

Build and install the package into the same pinned Service image, then select its entry-point name:

```toml
[provider_plugins]
enabled = ["acme"]
```

The distribution name (`a13n-provider-acme-example`), selected entry-point name (`acme`), and registered account types (`acme_web`, `acme_model`) are intentionally distinct. Its callable uses `@provider_plugin(api_version=1)` to declare the extension contract it was authored against; the literal must not be derived from the installed Service version. Registration creates only immutable metadata and factories. Account validation and operation-scoped runtime construction happen later through the existing Web and Model management/runtime paths.
