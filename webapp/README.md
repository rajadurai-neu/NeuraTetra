# cnat webapp

React + TypeScript front end (Vite). Talks to the API under `/api`.

| Page | Who | What |
|---|---|---|
| My drafts / Team queue / All notes | everyone (scoped by role) | Notes with status, critical/warning counts, filters |
| Note result | everyone with access | **Fix before signing** list, all findings with quotes, mark not applicable (with reason), quality measures, assessment history |
| Assess a note | everyone | Paste/type a note with tags, or upload `.txt` files |
| Dashboard | supervisor, QA, admin | Key numbers, current status, weekly trend, most common issues, breakdown by clinician |
| Rules | everyone | Active rule set and quality measures |

## Run locally
Start the API first (see the repo README), then:
```bash
cd webapp && npm install && npm run dev
```
Open http://localhost:5173 and pick a **dev user** at the top right (local only).

## Build
```bash
npm run build   # outputs dist/
```

## Azure
Deploys to **Azure Static Web Apps**. `staticwebapp.config.json` requires Microsoft Entra ID sign-in and routes
`/api/*` to the linked App Service backend. Fill in `<TENANT_ID>` and the `AAD_CLIENT_ID` / `AAD_CLIENT_SECRET`
app settings when setting it up.

Charts follow accessible-color rules: status always shows an icon and label, single-series charts use one blue,
and every chart has a "Show as table" view.
