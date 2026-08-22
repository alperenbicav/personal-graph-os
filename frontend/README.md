# Personal Graph OS — frontend

React 19 + TypeScript + Vite PWA served same-origin by the FastAPI backend under `/app/`
(see the repository-root README for the product and architecture overview).

```bash
npm install          # once
npm run dev          # Vite dev server with HMR (needs PGOS_DEV_CORS=1 on the backend)
npm run build        # tsc -b && vite build → dist/ (what the backend serves)
npm test             # Vitest + Testing Library unit/component tests
npm run typecheck    # tsc -b --noEmit (strict)
npm run lint         # oxlint
npm run test:e2e     # Playwright acceptance suite (build first)
```

Structure: `src/components/` one component per view/panel, `src/api/` typed REST client +
session/auth handling, `src/hooks/` extracted handler families, `src/lib/` small framework-
agnostic helpers, `e2e/` Playwright specs and harness scripts. TypeScript `strict` is on.
