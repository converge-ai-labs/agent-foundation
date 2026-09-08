# a13n Console

Private React and TypeScript application built with Vite. The entry page uses i18next with English (`en`) as the default and fallback language and Simplified Chinese (`zh-CN`) resources. A shared UI language selector remembers the selection in browser storage when available.

From the repository root:

```bash
make frontend-sync
pnpm --dir frontend --filter a13n-console dev
make frontend-check-all
```

The development server listens on `http://127.0.0.1:5173`. This scaffold consumes `a13n-ui` components and styles. It has no business pages, API calls, or deployment configuration. The design system showcase belongs to `frontend/packages/a13n-ui/dev`.
