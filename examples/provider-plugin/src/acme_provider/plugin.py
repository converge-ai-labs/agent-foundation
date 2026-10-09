"""One inert manifest contributes the Acme Environment definition."""

from a13n_harness.providers.plugins import ProviderManifest

from .environment import acme_environment

manifest = ProviderManifest(api_version=2, environment=(acme_environment,))
