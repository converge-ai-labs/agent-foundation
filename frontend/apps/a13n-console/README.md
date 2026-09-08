# a13n Console

Private React and TypeScript application built with Vite. The entry page uses i18next with English (`en`) as the default and fallback language and Simplified Chinese (`zh-CN`) resources. Language selection and persistence are not implemented.

From the repository root:

```bash
make frontend-sync
pnpm --dir frontend --filter a13n-console dev
make frontend-check-all
```

The development server listens on `http://127.0.0.1:5173`. This scaffold has no styles, shared components, business pages, API calls, or deployment configuration.
