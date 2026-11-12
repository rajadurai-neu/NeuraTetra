# Clinical Notes Assessment Tool (CNAT)

Reviews draft clinical notes against NYS OMH Article 31 / 14 NYCRR Part 599, OMH documentation
rules, SOAP format and clinical quality measures. **Assesses only: it never edits, signs or deletes EHR data.**

Dev EHR: OpenEMR 7.0.3 (synthetic data only).

## Repo layout (one repo, four parts)

| Folder | What | Depends on | Runs on Azure as |
|---|---|---|---|
| [`engine/`](engine/) | `cnat_engine` Python package: domain types, rule checks, Claude reviewer, scoring, pipeline. No web/DB code | – | (installed into api) |
| [`rulesets/`](rulesets/) | YAML rules (Part 599, OMH, SOAP, agency), quality measures, synthetic sample notes with expected results | – | (files loaded by api) |
| [`api/`](api/) | `cnat_api` Python package: FastAPI routes, sign-in/roles, database, services, OpenEMR connector, scheduler | engine, rulesets | App Service |
| [`webapp/`](webapp/) | React + TypeScript front end: queues, note result, assess a note, dashboard, rules | api | Static Web Apps |

```
engine/src/cnat_engine/
  domain/          NoteContext, Finding, Assessment
  rules/           ruleset loader, check functions, runner
  ai/              Claude reviewer + versioned prompt (prompts/v1.md)
  scoring.py       status + compliance/quality/overall scores
  pipeline.py      rules -> AI -> scoring
api/src/cnat_api/
  web/             FastAPI app, schemas, sign-in + role-based access
  services/        assess / re-assess, history, not-applicable, signing, queues
  db/              tables, repository, AI review cache, append-only audit log
  connectors/      OpenEMR read-only OAuth client
  providers/       Claude client (Azure AI Foundry | Anthropic API | none)
  jobs/            OpenEMR draft-note polling
  config.py, timefmt.py, main.py
api/scripts/       assess_note.py, openemr_register.py, openemr_probe.py
```

## Setup
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e engine -e "api[test]"
cp api/.env.example api/.env      # then fill in values
```

## Tests
```bash
.venv/bin/python -m pytest
```

## Run the API locally
```bash
CNAT_DEV_AUTH=1 CNAT_AI_PROVIDER=none .venv/bin/python -m uvicorn cnat_api.main:app --port 8010
```
Open http://localhost:8010/docs. Send `X-Dev-User` and `X-Dev-Role` (clinician | supervisor | qa | admin) headers.

## Run the web app locally
```bash
cd webapp && npm install && npm run dev
```
Open http://localhost:5173 and pick a dev user at the top right.

## Assess a note from the command line
```bash
.venv/bin/python api/scripts/assess_note.py rulesets/samples/poor_progress.json --no-ai
```
Drop `--no-ai` once the `CNAT_AI_*` settings are in `api/.env` (Claude in Azure AI Foundry).

## Connect to OpenEMR (OAuth, read-only)
```bash
.venv/bin/python api/scripts/openemr_register.py
```
Then, as an OpenEMR admin:
1. **Administration → System → API Clients**: enable "Clinical Notes Assessment Tool (Dev)".
2. **Administration → Config → Connectors**: turn on the Standard REST API, its **system scopes**, and the FHIR API; set Site Address Override to the server URL.

Check it with `.venv/bin/python api/scripts/openemr_probe.py`.

### Draft-note intake
With the client registered, the API pulls SOAP notes from OpenEMR and assesses the ones that changed
(unchanged drafts are skipped, so polling costs no AI calls). Settings in `api/.env`:
`OPENEMR_SYNC_MINUTES` (0 = off), `OPENEMR_LOOKBACK_DAYS`, `OPENEMR_DEFAULT_PROGRAM`.

- Run once now (admin/QA): `POST /api/admin/openemr/sync`
- Last run (admin/QA): `GET /api/admin/openemr/status`

#### Turning intake on in Azure
The deploy package never contains `.env` or `keys/`, so the App Service gets the same values as app settings,
with the private key as text in `OPENEMR_PRIVATE_KEY`:
```bash
.venv/bin/python api/scripts/openemr_azure_settings.py > openemr-azure.secret.txt
```
Paste that JSON into **App Service → Settings → Environment variables → App settings → Advanced edit**
(add to the existing list), **Apply**, then delete the file. Prefer a Key Vault reference for
`OPENEMR_CLIENT_SECRET` and `OPENEMR_PRIVATE_KEY` once Key Vault is set up. If the settings are incomplete,
the app still starts and logs `OpenEMR intake: disabled`.

OpenEMR's standard API has no modality, participants, service code, telehealth consent/location or
treatment plan. Notes from OpenEMR declare these as unavailable, and the rules that need them are skipped
rather than reported as false "missing" issues. Start/end times come from the matching appointment.

## Rules
Rule wording, severities, CPT time ranges and the documentation deadline are **starter values** and must be
reviewed by compliance staff. Bump `version` in `rulesets/manifest.yaml` on every change; each assessment stores it.

## Security notes
- No user passwords are used for OpenEMR. The tool signs in with its own key (`api/keys/`, gitignored).
- Only `.read` scopes are allowed; the client has no write methods.
- The OpenEMR server is plain HTTP today: `OPENEMR_ALLOW_INSECURE_HTTP=1` is for synthetic dev data only.

## Known gap
OpenEMR's APIs don't expose e-Sign status (draft vs signed). The next phase adds a small read-only
OpenEMR module endpoint (or a DB view) for signature status.

## Deploying to Azure

See [deploy/README.md](deploy/README.md): one App Service (UI + API) plus Azure SQL, deployed by `azure-pipelines.yml` or `deploy/azure.sh`.
