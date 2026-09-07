# a13n

TypeScript SDK package for a13n Service.

## Status

This `0.0.x` package reserves the stable npm package and module names while the service API is being designed. It intentionally exposes no client API yet. Generated models and transports will be added only after the service contract is stable enough to support compatibility guarantees.

## Installation

```bash
npm install a13n
```

```typescript
import "a13n";
```

## Development

Run the TypeScript SDK checks from the repository root:

```bash
make sdk-typescript-check
```

## Publishing

The first public version must be published locally as an RC to establish the npm package while preserving the stable version for the release workflow. For the initial A13N release:

```bash
python3 scripts/prepare-release-version.py a13n-typescript 0.0.3-rc.1
cd sdk/typescript
npm ci
npm run check:all
npm publish --access public --tag rc
```

Run this from a clean checkout of the reviewed release commit and discard the injected local version changes afterward. The publishing account must be able to create the unscoped `a13n` package and either complete 2FA or use a temporary granular access token that can bypass 2FA. Revoke the bootstrap token immediately after trusted publishing is configured.

Configure the newly created package to trust the exact GitHub workflow and Environment. `npm trust` requires npm 11.15 or newer and interactive account authentication with 2FA; a bypass-2FA granular token cannot configure trust:

```bash
npx -y npm@11.19.0 trust github a13n \
  --repo converge-ai-labs/agent-foundation \
  --file release-a13n-typescript.yml \
  --environment a13n-typescript-npm \
  --allow-publish \
  --yes

npx -y npm@11.19.0 trust list a13n
```

After the bootstrap RC and trust configuration succeed, publish stable `0.0.3` from `.github/workflows/release-a13n-typescript.yml`. Subsequent versions also use this Trusted Publishing workflow. Push `release/a13n/typescript/<version>`, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`; the workflow injects that version into `package.json` and `package-lock.json` in its ephemeral checkout. RCs publish under the npm `rc` dist-tag and never advance `latest`.

## License

Licensed under the Apache License 2.0.
