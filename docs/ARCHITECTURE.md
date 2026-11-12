# CNAT architecture

The Clinical Notes Assessment Tool reviews draft clinical notes against NYS OMH Article 31 /
14 NYCRR Part 599, OMH documentation rules, SOAP format and clinical quality measures.
**It only reads and assesses. It never edits, signs or deletes EHR data.**

Diagrams are [Mermaid](https://mermaid.js.org/); GitHub draws them when you open this file.

- [1. System overview](#1-system-overview)
- [2. Code structure](#2-code-structure)
- [3. OpenEMR intake flow](#3-openemr-intake-flow)
- [4. Assessment pipeline](#4-assessment-pipeline)
- [5. Sign-in and access](#5-sign-in-and-access)
- [6. Data model](#6-data-model)
- [7. Build and deploy](#7-build-and-deploy)
- [8. Key design decisions](#8-key-design-decisions)

---

## 1. System overview

Everything runs in **one Azure App Service**: it serves the React UI and the FastAPI API, and runs the
OpenEMR poller in the background. Data lives in **Azure SQL**. The AI review is pluggable.

```mermaid
flowchart LR
    user["Clinicians, supervisors,<br/>QA and admins<br/>(web browser)"]

    subgraph azure["Azure"]
        subgraph app["App Service (Python 3.13)"]
            ui["React web UI<br/>queues, note results,<br/>dashboard, rules, sync"]
            api["FastAPI API<br/>/api/*"]
            poller["OpenEMR poller<br/>every 5 min"]
            engine["Assessment engine<br/>rules + AI + scoring"]
        end
        sql[("Azure SQL<br/>notes, assessments,<br/>findings, audit log")]
    end

    subgraph ehr["OpenEMR 7.0.3"]
        fhir["FHIR API<br/>read-only"]
    end

    subgraph ai["AI review (one of)"]
        anth["Anthropic API<br/>Claude Opus 5.5<br/>(in use)"]
        foundry["Claude in<br/>Azure AI Foundry"]
        aoai["Azure OpenAI<br/>GPT"]
    end

    gh["GitHub repo +<br/>GitHub Actions"]

    user -- "HTTPS, sign-in" --> ui
    ui -- "JSON" --> api
    api --> engine
    poller --> engine
    poller -- "OAuth, GET only" --> fhir
    engine --> sql
    api --> sql
    engine -- "note text +<br/>quality measures" --> anth
    engine -.-> foundry
    engine -.-> aoai
    gh -- "test, package,<br/>zip deploy" --> app
```

| Part | Technology | Notes |
|---|---|---|
| Web UI | React 18, TypeScript, Vite, Recharts | Built into `webapp/dist`, served by the API |
| API | FastAPI, Uvicorn | One process; also runs the poller thread |
| Engine | Python package `cnat_engine` | No web or database code; pure assessment logic |
| Rule sets | YAML in `rulesets/` | Versioned (`2026.11.0`); every assessment stores the version |
| Database | Azure SQL Basic (SQLite locally) | SQLAlchemy 2.0 |
| EHR | OpenEMR 7.0.3 FHIR R4 | Clinical Notes form → `DocumentReference` |
| AI | Anthropic API (`claude-opus-5-5`) | Switchable by setting `CNAT_AI_PROVIDER` |
| CI/CD | GitHub Actions | `.github/workflows/deploy.yml` |

---

## 2. Code structure

One repository, four parts. Arrows point from a part to what it depends on.

```mermaid
flowchart TB
    subgraph webapp["webapp/"]
        pages["pages/<br/>Queue, NoteResult, NewNote,<br/>Dashboard, Rules, Sync, SignIn"]
        client["api/client.ts"]
    end

    subgraph apipkg["api/"]
        web["web/<br/>routes, schemas, security,<br/>password_auth"]
        services["services/<br/>assessments, reports"]
        jobs["jobs/<br/>openemr_sync<br/>(sources + scheduler)"]
        connectors["connectors/openemr/<br/>auth, client, mappers"]
        providers["providers/<br/>ai.py, azure_openai.py"]
        db["db/<br/>tables, repository, session"]
    end

    subgraph enginepkg["engine/"]
        pipeline["pipeline.py"]
        rules["rules/<br/>loader, checks, runner"]
        aimod["ai/<br/>reviewer + prompts/v1.md"]
        scoring["scoring.py"]
        domain["domain/<br/>NoteContext, Finding, Assessment"]
    end

    rulesets[("rulesets/<br/>YAML rules + quality measures<br/>+ synthetic samples")]

    pages --> client --> web
    web --> services
    jobs --> connectors
    jobs --> services
    services --> db
    services --> pipeline
    providers --> aimod
    pipeline --> rules
    pipeline --> aimod
    pipeline --> scoring
    rules --> rulesets
    rules --> domain
    aimod --> domain
```

---

## 3. OpenEMR intake flow

Every 5 minutes, and whenever an admin clicks **Sync now**, CNAT reads recent Clinical Notes from OpenEMR
and assesses only the ones that are new or changed.

```mermaid
sequenceDiagram
    autonumber
    participant S as Scheduler / Sync now
    participant Src as FhirSource
    participant O as OpenEMR (FHIR)
    participant Svc as AssessmentService
    participant DB as Azure SQL

    S->>Src: fetch(since = today - 14 days)
    Src->>O: POST /oauth2/default/token (signed JWT)
    O-->>Src: access token (system/*.read scopes)
    Src->>O: GET DocumentReference?category=clinical-note&date=ge...
    O-->>Src: notes (base64 text with S:/O:/A:/P:)
    loop each note
        Src->>O: GET Patient, Encounter, Organization, Practitioner (cached)
        O-->>Src: chart number, clinic, author (if any)
        Src->>Src: map to NoteContext<br/>(fields OpenEMR lacks = "unavailable")
    end
    Src-->>S: notes + per-note failures
    S->>S: group by client, oldest first,<br/>attach up to 5 prior notes (clone check)
    loop each note
        S->>Svc: assess_note(note)
        Svc->>DB: upsert note, compare content hash
        alt text unchanged
            Svc-->>S: reuse stored assessment (no AI call)
        else new or changed
            Svc->>Svc: run assessment pipeline
            Svc->>DB: save assessment, findings, scores, audit entry
            Svc-->>S: assessed
        end
    end
    S->>S: store SyncReport (shown on the OpenEMR sync page)
```

Why FHIR and the Clinical Notes form: OpenEMR 7.0.3 accepts system (no-user) tokens only on its FHIR
API, and the SOAP form is not exposed over FHIR. `OPENEMR_API=standard` switches to the SOAP form for
OpenEMR versions that allow system tokens on the Standard API.

---

## 4. Assessment pipeline

The same pipeline runs for OpenEMR notes, pasted notes, uploaded files and re-assessments.

```mermaid
flowchart TD
    start(["NoteContext"]) --> rules["Step 1: Deterministic rules<br/>Part 599, OMH, SOAP, agency<br/>(always re-run: timeliness changes)"]
    start --> hash["Step 2: AI input hash<br/>text + sections + context +<br/>rule/prompt/model versions"]
    hash --> cached{"Result for this<br/>hash already stored?"}
    cached -- yes --> reuse["Reuse stored AI review<br/>(same note = same result)"]
    cached -- no --> aicall["Call AI reviewer<br/>fixed output structure"]
    aicall --> refusal{"Refused?"}
    refusal -- "yes (Anthropic API)" --> fallback["Server-side fallback<br/>to another Claude model"]
    refusal -- no --> ground
    fallback --> ground["Grounding filter<br/>drop findings whose quote<br/>is not in the note"]
    ground --> store["Store in AI cache"]
    store --> merge
    reuse --> merge
    rules --> merge["Step 3: Merge findings<br/>apply Not-applicable overrides"]
    merge --> score["Step 4: Score<br/>compliance = passed / checked rules<br/>quality = measure points / max<br/>overall = 0.6 x compliance + 0.4 x quality"]
    score --> status{"Step 5: Status"}
    status -- "any open Critical" --> fail["Does not meet standards"]
    status -- "any open Warning" --> warn["Needs attention"]
    status -- otherwise --> ok["Meets standards"]
```

| Step | Where | What it guarantees |
|---|---|---|
| Rules | `engine/rules/checks.py`, `rulesets/*.yaml` | Same input → same result. The risk-language safety check never depends on AI |
| Unavailable fields | `NoteContext.unavailable_fields` | Rules needing data the EHR can't supply return N/A, not false "missing" issues |
| AI review | `engine/ai/reviewer.py`, `api/providers/` | Claude (Anthropic API or Foundry) or GPT (Azure OpenAI); an unconfigured provider means rules only |
| Grounding | `to_review_result` | Every AI finding quotes the note exactly, or says `ABSENT` |
| Versions | stored on each assessment | Rule set version, prompt version and model, for audit and reproducibility |

---

## 5. Sign-in and access

```mermaid
flowchart TD
    req(["Request to /api/*"]) --> ea{"X-MS-CLIENT-PRINCIPAL header?<br/>(App Service + Entra ID)"}
    ea -- yes --> user["User + roles from Entra ID"]
    ea -- no --> cookie{"Valid cnat_session cookie?<br/>(built-in sign-in)"}
    cookie -- yes --> lookup["Look up account in CNAT_USERS<br/>(removed users lose access at once)"]
    lookup --> user
    cookie -- no --> dev{"CNAT_DEV_AUTH=1<br/>and X-Dev-User header?<br/>(local only)"}
    dev -- yes --> user
    dev -- no --> r401["401 Sign in required"]
    user --> roles{"Has a CNAT role?"}
    roles -- no --> r403["403 No role"]
    roles -- yes --> scope["Scope every query by role"]
    scope --> clin["Clinician: own notes"]
    scope --> sup["Supervisor: own team's notes"]
    scope --> qa["QA / Admin: all notes,<br/>reports, OpenEMR sync"]
```

- **In use now:** built-in sign-in. Accounts live in the `CNAT_USERS` app setting with PBKDF2-SHA256
  password hashes, the cookie is signed with `CNAT_SESSION_SECRET`, and 5 wrong passwords lock a username
  for 5 minutes.
- **When Entra ID is available:** turn on App Service Authentication. Its identity header takes priority
  automatically.
- Other people's notes return 404, so a user can't tell whether a note exists.

---

## 6. Data model

```mermaid
erDiagram
    NOTES ||--o{ ASSESSMENTS : "assessed as"
    ASSESSMENTS ||--o{ FINDINGS : has
    ASSESSMENTS ||--o{ MEASURE_SCORES : has
    NOTES ||--o{ NOT_APPLICABLE : "rule marked N/A"
    NOTES {
        string id PK "openemr:docref:... or manual:..."
        string source
        string client_id
        string clinician_name
        string supervisor
        date date_of_service
        string content_hash "detects changed drafts"
        text text
        bool is_signed
    }
    ASSESSMENTS {
        int id PK
        string note_id FK
        bool is_current
        string trigger "on_demand, schedule, ..."
        string status
        float overall_score
        string ruleset_version
        string prompt_version
        string model
        string input_hash
        datetime assessed_at
    }
    FINDINGS {
        int id PK
        int assessment_id FK
        string rule_id
        string severity "critical, warning, info"
        string origin "rule or ai"
        text evidence "exact quote"
    }
    MEASURE_SCORES {
        int id PK
        int assessment_id FK
        string measure
        int score "0 to 3"
    }
    NOT_APPLICABLE {
        int id PK
        string note_id FK
        string rule_id
        text reason
        string marked_by
    }
    AI_REVIEW_CACHE {
        string input_hash PK
        json result
    }
    AUDIT_LOG {
        int id PK
        datetime at
        string actor
        string action "note_retrieved, assessment_run, results_viewed, ..."
        string record_id
    }
```

`AUDIT_LOG` is append-only. `AI_REVIEW_CACHE` is keyed by the AI input hash, so an unchanged note never
calls the AI twice.

---

## 7. Build and deploy

```mermaid
flowchart LR
    dev["git push to main"] --> gha["GitHub Actions<br/>Test and deploy"]
    subgraph job1["Job: test-and-package"]
        t1["Python 3.13 + Node 20"] --> t2["pytest<br/>(engine + api)"]
        t2 --> t3["deploy/package.sh<br/>build UI, zip app,<br/>refuse .env / keys / *.db"]
    end
    subgraph job2["Job: deploy (main only)"]
        d1["azure/webapps-deploy<br/>(publish profile secret)"] --> d2["App Service build (Oryx)<br/>pip install ./engine ./api"]
        d2 --> d3["Start: uvicorn cnat_api.main:app"]
        d3 --> d4["Wait for /api/health"]
    end
    gha --> job1 --> job2
    pr["Pull request"] --> job1
```

Configuration lives in **App Service app settings**, never in the package:

| Group | Settings |
|---|---|
| Database | `CNAT_DATABASE_URL` |
| Sign-in | `CNAT_USERS`, `CNAT_SESSION_SECRET` |
| AI review | `CNAT_AI_PROVIDER`, `CNAT_AI_MODEL`, plus the provider's key settings |
| OpenEMR | `OPENEMR_BASE_URL`, `OPENEMR_CLIENT_ID`, `OPENEMR_KEY_ID`, `OPENEMR_PRIVATE_KEY_B64`, `OPENEMR_SCOPES`, `OPENEMR_SYNC_MINUTES`, ... |

`azure-pipelines.yml` (Azure DevOps) is kept as an alternative; it needs hosted parallel jobs.
`deploy/azure.sh` can create the Azure resources from the command line. See [deploy/README.md](../deploy/README.md).

---

## 8. Key design decisions

| Decision | Why |
|---|---|
| Assess only, GET-only EHR client | The tool must never change clinical records |
| Deterministic rules + AI | Rules give repeatable compliance checks; AI judges quality. The risk safety net doesn't depend on AI |
| Fixed AI output structure + exact-quote check | Results are comparable across notes and models, and the AI can't invent content |
| AI result cache by input hash | Same note + same versions = same score; polling costs no AI calls for unchanged notes |
| Versioned rule sets and prompts | Every assessment records what it was judged against |
| Unavailable fields instead of false failures | EHR APIs lack some fields (modality, service code, ...); those rules are skipped, not failed |
| One App Service for UI + API | Simplest Azure setup: one resource, one sign-in, no cross-site calls |
| Pluggable AI provider | Claude via Anthropic API today; Foundry or Azure OpenAI by changing settings |
| All times shown as 12-hour Eastern | e.g. `11/12/2026 2:35 AM ET` |

### Known gaps
- OpenEMR's APIs don't expose e-Sign status, so OpenEMR notes are treated as drafts.
- The dev OpenEMR links new Clinical Notes to the wrong patient (a data issue in that instance) and sends
  no note author, so OpenEMR notes are visible to QA and Admin only.
- The OpenEMR server is plain HTTP and uses synthetic data only. Turn on HTTPS before any real data.
- Before real patient data: a BAA with the AI provider, Entra ID sign-in, and database migrations (Alembic).
