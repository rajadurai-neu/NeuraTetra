# Clinical Notes Assessment Tool (CNAT)

Reviews draft clinical notes against NYS OMH Article 31 / 14 NYCRR Part 599, OMH documentation
rules, SOAP format and clinical quality measures. **Assesses only: it never edits, signs or deletes EHR data.**

Dev EHR: OpenEMR 7.0.3 (synthetic data only).

**Architecture and flow diagrams:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Repo layout (one repo, four parts)

| Folder | What | Depends on | Runs on Azure as |
|---|---|---|---|
| [`engine/`](engine/) | `cnat_engine` Python package: domain types, rule checks, AI reviewer, scoring, pipeline. No web/DB code | – | (installed into api) |
| [`rulesets/`](rulesets/) | YAML rules (Part 599, OMH, SOAP, agency), quality measures, synthetic sample notes with expected results | – | (files loaded by api) |
| [`api/`](api/) | `cnat_api` Python package: FastAPI routes, sign-in/roles, database, services, OpenEMR connector, scheduler | engine, rulesets | App Service |
| [`webapp/`](webapp/) | React + TypeScript front end: queues, note result, assess a note, dashboard, rules, OpenEMR sync | api | App Service (served by api) |

```
engine/src/cnat_engine/
  domain/          NoteContext, Finding, Assessment
  rules/           ruleset loader, check functions, runner
  ai/              AI reviewer (Claude) + versioned prompt (prompts/v1.md)
  scoring.py       status + compliance/quality/overall scores
  pipeline.py      rules -> AI -> scoring
api/src/cnat_api/
  web/             FastAPI app, schemas, sign-in (built-in or Entra ID) + role-based access
  services/        assess / re-assess, history, not-applicable, signing, queues
  db/              tables, repository, AI review cache, append-only audit log
  connectors/      OpenEMR read-only OAuth client, FHIR + Standard API mappers
  providers/       AI provider: Anthropic API | Azure AI Foundry | Azure OpenAI | none
  jobs/            OpenEMR draft-note polling (FHIR or Standard API source)
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
1. **Administration → Config → Connectors**: turn on **Enable OpenEMR Standard FHIR REST API** and **Enable OpenEMR FHIR System Scopes**, then Save.
2. **Administration → System → API Clients**: enable "Clinical Notes Assessment Tool (FHIR)".

Check it with `.venv/bin/python api/scripts/openemr_probe.py`.

### Draft-note intake
CNAT reads notes written in OpenEMR's **Clinical Notes** form (FHIR `DocumentReference`, category `clinical-note`).
Clinicians start each section on its own line with `S:`, `O:`, `A:` and `P:`.
OpenEMR 7.0.3 accepts system (no-user) tokens only on FHIR, so the SOAP form, which is Standard API only, can't be read
with this kind of access. `OPENEMR_API=standard` switches to the SOAP form for OpenEMR versions that allow it.

Unchanged notes are skipped, so polling costs no AI calls. Settings in `api/.env`:
`OPENEMR_SYNC_MINUTES` (0 = off), `OPENEMR_LOOKBACK_DAYS`, `OPENEMR_DEFAULT_PROGRAM`.

- Only drafts are assessed. QA retrospective review of signed notes: `POST /api/admin/openemr/retro`
- Run once now (admin/QA): `POST /api/admin/openemr/sync`. Both also have buttons on the **OpenEMR sync** page
- Last run (admin/QA): `GET /api/admin/openemr/status`

Session times come from a same-day FHIR Appointment, and modality from the visit's class. OpenEMR doesn't
supply participants, service code, telehealth consent/location or treatment plan over FHIR, and on the dev
server it gives no note author. Notes from OpenEMR declare these as unavailable, so the
rules that need them are skipped instead of reported as false "missing" issues.

## Rules
Rule wording, severities, CPT time ranges, the documentation deadline and the **required elements per note type**
(`rulesets/note_types.yaml`) are **starter values** and must be reviewed by compliance staff. Bump `version` in `rulesets/manifest.yaml` on every change; each assessment stores it.

## Security notes
- No user passwords are used for OpenEMR. The tool signs in with its own key (`api/keys/`, gitignored).
- Only `.read` scopes are allowed; the client has no write methods.
- The OpenEMR server is plain HTTP today: `OPENEMR_ALLOW_INSECURE_HTTP=1` is for synthetic dev data only.

## Known gap
OpenEMR's APIs don't expose e-Sign status (draft vs signed). The next phase adds a small read-only
OpenEMR module endpoint (or a DB view) for signature status.

## Deploying to Azure

See [deploy/README.md](deploy/README.md): one App Service (UI + API) plus Azure SQL, deployed by `azure-pipelines.yml` or `deploy/azure.sh`.
