# Deploy to Azure

Simple setup: everything runs in **one App Service**, which serves both the web UI and the API.
Microsoft Entra ID sign-in is handled by App Service ("Easy Auth"), and data is stored in **Azure SQL (Basic)**.

```
Browser ──Entra sign-in──▶ App Service (Linux, Python 3.13)
                             ├─ /         React web UI (webapp/dist)
                             ├─ /api/*    FastAPI
                             └─ OpenEMR poller (optional, background thread)
                                  │
                   Azure SQL ◀────┤
     Claude in Azure AI Foundry ◀─┘ (optional)
```

| Resource | Tier | Approx. cost |
|---|---|---|
| App Service plan | B1 Linux | ~$13/month |
| Azure SQL database | Basic (5 DTU) | ~$5/month |

## First time

You need the Azure CLI (`brew install azure-cli`) and Node 18+.

```bash
az login
deploy/azure.sh setup     # creates the resource group, App Service, SQL, Entra app + roles, sign-in
deploy/azure.sh deploy    # builds the UI, packages the app, deploys it
```

`setup` gives the person who ran it the **Admin** role. To add other people, go to Entra ID → Enterprise applications → *CNAT (…)* → Users and groups, and assign each person Clinician, Supervisor, QA or Admin.

## Sign-in

There are two ways to sign in:

- **Built-in sign-in (simplest; no Entra permissions needed).** The app shows its own sign-in page. Accounts live in one App Service setting, and passwords are stored only as hashes.
- **Microsoft Entra ID (App Service Authentication).** Use this when you can create an app registration. If it's turned on, it takes priority automatically.

### Built-in sign-in setup
1. On your machine, run this once per person. It asks for the username, the full name used on notes, the roles and a password:
   ```bash
   .venv/bin/python api/scripts/hash_password.py
   ```
   It prints one JSON object per person, plus a random session secret the first time.
2. **App Service → Settings → Environment variables → App settings**, add:

   | Name | Value |
   |---|---|
   | `CNAT_USERS` | a JSON list of those objects: `[{"username": "admin", ...}, {"username": "jsmith", ...}]` |
   | `CNAT_SESSION_SECRET` | the random secret (at least 32 characters, keep it private) |

3. Click **Apply**. The app restarts, and the site shows the sign-in page.

- **Roles:** `clinician`, `supervisor`, `qa`, `admin`. The full name must match the clinician or supervisor name on notes, because that's how each person's queue is scoped.
- **Lockout:** 5 wrong passwords lock that username for 5 minutes.
- **Sessions** last 12 hours (`CNAT_SESSION_HOURS`).
- **To remove someone:** delete their entry from `CNAT_USERS`. They're signed out on their next request.

## CI/CD with GitHub Actions (recommended)

[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml) runs on every push to `main`. It runs the tests, packages the app and deploys it. Pull requests only run the tests and build the package.

One-time setup:
1. **App Service → Settings → Configuration → General settings**: turn **SCM Basic Auth Publishing Credentials** **On**, then **Save**.
2. **App Service → Overview → Download publish profile** (a `.PublishSettings` file).
3. **GitHub → repo → Settings → Secrets and variables → Actions → New repository secret**:
   name `AZURE_WEBAPP_PUBLISH_PROFILE`, value = the whole contents of that file.
4. Delete the downloaded file. It contains a deploy password.
5. Make sure the App Service has the startup command and the `SCM_DO_BUILD_DURING_DEPLOYMENT=true` setting (see the manual setup steps).

The app name is set as `WEBAPP_NAME` at the top of the workflow. To require an approval before deploying, add reviewers under **Settings → Environments → production**.

## CI/CD with Azure Pipelines (alternative)

Use this instead of GitHub Actions only if your Azure DevOps organization has hosted parallel jobs. New organizations must request the free grant first.


[`azure-pipelines.yml`](../azure-pipelines.yml) at the repo root runs on every push to `main`. It runs the tests, builds and packages the app with `deploy/package.sh`, and deploys it to the App Service. Pull requests run only the tests and the package step.

One-time setup in Azure DevOps:
1. **Project settings → Service connections → New → Azure Resource Manager**, scoped to the `cnat-rg` resource group.
2. **Pipelines → New pipeline → GitHub → this repo**. It picks up `azure-pipelines.yml`.
3. Set `azureServiceConnection` and `webAppName` at the top of `azure-pipelines.yml`. No variable group is needed.
4. Optional: under **Environments → cnat-production → Approvals and checks**, add an approval before deploys.

The pipeline only deploys code. The App Service, Azure SQL and sign-in are created once, either by `deploy/azure.sh setup` or by hand.

## After a code change (without the pipeline)

```bash
deploy/azure.sh deploy
```

## Turning on Claude (Azure AI Foundry)

Deploy a Claude model in Azure AI Foundry, then run:

```bash
source deploy/.azure-state
az webapp config appsettings set -g $RG -n $APP --settings \
  CNAT_AI_PROVIDER=foundry CNAT_AI_FOUNDRY_RESOURCE=<resource-name> \
  CNAT_AI_FOUNDRY_API_KEY=<key> CNAT_AI_MODEL=<deployment-name>
```

## OpenEMR intake

Set the `OPENEMR_*` values from `api/.env.example` as app settings in the same way.
Put the private key in an app setting or Key Vault, never in the package. `deploy` refuses to ship any file that contains a private key.

## Logs

```bash
source deploy/.azure-state && az webapp log tail -g $RG -n $APP
```
