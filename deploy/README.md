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

## CI/CD with Azure Pipelines

[`azure-pipelines.yml`](../azure-pipelines.yml) at the repo root runs on every push to `main`. It runs the tests, builds and packages the app with `deploy/package.sh`, and deploys it to the App Service. Pull requests run only the tests and the package step.

One-time setup in Azure DevOps:
1. **Project settings → Service connections → New → Azure Resource Manager**, scoped to the `cnat-rg` resource group.
2. **Pipelines → New pipeline → GitHub → this repo**. It picks up `azure-pipelines.yml`.
3. Add two pipeline variables: `azureServiceConnection` (the connection name) and `webAppName` (the App Service name).
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
