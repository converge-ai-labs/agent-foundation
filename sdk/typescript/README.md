# a13n-sdk

TypeScript SDK package for Agent Foundation Service.

## Status

This `0.0.x` package reserves the stable npm package and module names while the service API is being designed. It intentionally exposes no client API yet. Generated models and transports will be added only after the service contract is stable enough to support compatibility guarantees.

## Installation

```bash
npm install a13n-sdk
```

```typescript
import "a13n-sdk";
```

## Development

Run the TypeScript SDK checks from the repository root:

```bash
make sdk-typescript-check
```

## Publishing

The first public version must be published locally as an RC to establish the npm package while preserving the stable version for the release workflow. For the initial A13N release:

```bash
python3 scripts/prepare-release-version.py sdk-typescript 0.0.3-rc.1
cd sdk/typescript
npm ci
npm run check:all
npm publish --access public --tag rc
```

Run this from a clean checkout of the reviewed release commit and discard the injected local version changes afterward. The publishing account must be able to create the unscoped `a13n-sdk` package and either complete 2FA or use a temporary granular access token that can bypass 2FA. Revoke the bootstrap token immediately after trusted publishing is configured.

Configure the newly created package to trust the exact GitHub workflow and Environment. `npm trust` requires npm 11.15 or newer and interactive account authentication with 2FA; a bypass-2FA granular token cannot configure trust:

```bash
npx -y npm@11.19.0 trust github a13n-sdk \
  --repo converge-ai-labs/agent-foundation \
  --file release-sdk-typescript.yml \
  --environment sdk-typescript-npm \
  --allow-publish \
  --yes

npx -y npm@11.19.0 trust list a13n-sdk
```

After the bootstrap RC and trust configuration succeed, publish stable `0.0.3` from `.github/workflows/release-sdk-typescript.yml`. Subsequent versions also use this Trusted Publishing workflow. Push `release/sdk/typescript/<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`; the workflow injects that version into `package.json` and `package-lock.json` in its ephemeral checkout. RCs publish under the npm `rc` dist-tag and never advance `latest`.

## License

Licensed under the Apache License 2.0.
