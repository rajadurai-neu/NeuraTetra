# Clinical Notes Assessment Tool (CNAT)

Reviews draft clinical notes against NYS OMH Article 31 / 14 NYCRR Part 599, OMH documentation
rules, SOAP format and clinical quality measures. **Assesses only: it never edits, signs or deletes EHR data.**

Dev EHR: OpenEMR 7.0.3 (synthetic data only).

## Layout
```
rulesets/                      YAML rules (Part 599, OMH, SOAP, agency) + quality measures, versioned
backend/cnat/
  domain/                      NoteContext, Finding, Assessment (plain data)
  engine/rules/                rule loader, check functions, runner
  engine/ai/                   Claude reviewer + versioned prompt (prompts/v1.md)
  engine/scoring.py            status + compliance/quality/overall scores
  engine/pipeline.py           rules -> AI -> scoring (one pipeline for every note source)
  providers/ai.py              Claude client: Azure AI Foundry | Anthropic API | none
  connectors/openemr/          read-only OAuth client for OpenEMR
  db/ services/ api/ jobs/     next phases
backend/tests/                 tests + synthetic sample notes with expected results
scripts/                       assess_note.py, openemr_register.py, openemr_probe.py
```

## Assess a note
```bash
backend/.venv/bin/python scripts/assess_note.py backend/tests/samples/poor_progress.json --no-ai
```
Drop `--no-ai` once `CNAT_AI_*` settings are in `.env` (Claude in Azure AI Foundry).

## Connect to OpenEMR (OAuth, read-only)
```bash
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/python scripts/openemr_register.py
```
Then, as an OpenEMR admin:
1. **Administration → System → API Clients**: enable "Clinical Notes Assessment Tool (Dev)".
2. **Administration → Config → Connectors**: turn on the Standard REST API, its **system scopes**, and the FHIR API; set Site Address Override to the server URL.

Then check it:
```bash
backend/.venv/bin/python scripts/openemr_probe.py
```

## Security notes
- No user passwords are used. The tool authenticates with its own key (`keys/`, gitignored).
- Only `.read` scopes are allowed; the code refuses write scopes and has no write methods.
- The server is plain HTTP right now, so `OPENEMR_ALLOW_INSECURE_HTTP=1` is for synthetic dev data only. Turn on HTTPS and set it to 0.

## Known gap
OpenEMR's APIs don't expose **e-Sign status** (draft vs signed). Phase 1 will add a small read-only
OpenEMR custom module endpoint (or a DB view) for signature status and save events.

## Tests
```bash
cd backend && .venv/bin/python -m pytest -q
```
