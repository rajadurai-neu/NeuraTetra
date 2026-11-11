#!/usr/bin/env bash
# Deploy CNAT to Azure, the simple way:
#   one App Service (Linux, Python) serves the web UI + API, signed in with Microsoft Entra ID (Easy Auth)
#   one Azure SQL database (Basic tier)
#   Claude via Azure AI Foundry (optional; set the CNAT_AI_* settings later)
#
#   deploy/azure.sh setup    create the Azure resources once (safe to re-run)
#   deploy/azure.sh deploy   build the web UI, package the app and push it (run after every change)
#   deploy/azure.sh info     print the site URL and resource names
#
# Names are saved in deploy/.azure-state (not committed). Override with env vars on the first run,
# e.g.  LOCATION=eastus deploy/azure.sh setup
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/deploy/.azure-state"
[ -f "$STATE" ] && source "$STATE"

SUFFIX="${SUFFIX:-$(openssl rand -hex 3)}"
RG="${RG:-cnat-rg}"
LOCATION="${LOCATION:-centralindia}"
PLAN="${PLAN:-cnat-plan}"
PLAN_SKU="${PLAN_SKU:-B1}"
APP="${APP:-cnat-$SUFFIX}"
SQL_SERVER="${SQL_SERVER:-cnat-sql-$SUFFIX}"
SQL_DB="${SQL_DB:-cnat}"
SQL_ADMIN="${SQL_ADMIN:-cnatadmin}"
PYTHON="${PYTHON:-PYTHON:3.13}"
ENTRA_APP_NAME="${ENTRA_APP_NAME:-CNAT ($APP)}"

save_state() {
  cat > "$STATE" <<EOF
SUFFIX=$SUFFIX
RG=$RG
LOCATION=$LOCATION
PLAN=$PLAN
PLAN_SKU=$PLAN_SKU
APP=$APP
SQL_SERVER=$SQL_SERVER
SQL_DB=$SQL_DB
SQL_ADMIN=$SQL_ADMIN
PYTHON=$PYTHON
ENTRA_APP_ID=${ENTRA_APP_ID:-}
EOF
}

say() { printf '\n==> %s\n' "$*"; }

setup() {
  az account show -o none || { echo "Run 'az login' first."; exit 1; }
  save_state

  say "Resource group $RG ($LOCATION)"
  az group create -n "$RG" -l "$LOCATION" -o none

  say "App Service plan $PLAN ($PLAN_SKU, Linux)"
  az appservice plan show -g "$RG" -n "$PLAN" -o none 2>/dev/null ||
    az appservice plan create -g "$RG" -n "$PLAN" --is-linux --sku "$PLAN_SKU" -o none

  say "Web app $APP ($PYTHON)"
  az webapp show -g "$RG" -n "$APP" -o none 2>/dev/null ||
    az webapp create -g "$RG" -p "$PLAN" -n "$APP" --runtime "$PYTHON" -o none
  az webapp config set -g "$RG" -n "$APP" --always-on true --http20-enabled true --min-tls-version 1.2 \
    --startup-file "python -m uvicorn cnat_api.main:app --host 0.0.0.0 --port 8000" -o none
  az webapp update -g "$RG" -n "$APP" --https-only true -o none

  say "Azure SQL $SQL_SERVER / $SQL_DB (Basic)"
  if ! az sql server show -g "$RG" -n "$SQL_SERVER" -o none 2>/dev/null; then
    SQL_PASSWORD="Cn1-$(openssl rand -hex 16)"   # stored only in the app's settings
    az sql server create -g "$RG" -n "$SQL_SERVER" -l "$LOCATION" \
      --admin-user "$SQL_ADMIN" --admin-password "$SQL_PASSWORD" -o none
    az sql server firewall-rule create -g "$RG" -s "$SQL_SERVER" -n AllowAzureServices \
      --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none
    DB_URL="mssql+pyodbc://$SQL_ADMIN:$SQL_PASSWORD@$SQL_SERVER.database.windows.net:1433/$SQL_DB?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no"
    az webapp config appsettings set -g "$RG" -n "$APP" --settings "CNAT_DATABASE_URL=$DB_URL" -o none
  fi
  az sql db show -g "$RG" -s "$SQL_SERVER" -n "$SQL_DB" -o none 2>/dev/null ||
    az sql db create -g "$RG" -s "$SQL_SERVER" -n "$SQL_DB" --service-objective Basic -o none

  say "App settings"
  az webapp config appsettings set -g "$RG" -n "$APP" -o none --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    CNAT_DEV_AUTH=0 \
    CNAT_AI_PROVIDER="${CNAT_AI_PROVIDER:-none}" \
    TZ=America/New_York

  setup_sign_in
  save_state
  info
}

setup_sign_in() {
  local host="https://$APP.azurewebsites.net"
  local tenant; tenant="$(az account show --query tenantId -o tsv)"

  say "Entra ID app registration with roles Clinician / Supervisor / QA / Admin"
  if [ -z "${ENTRA_APP_ID:-}" ]; then
    local roles; roles="$(mktemp)"
    python3 - "$roles" <<'PY'
import json, sys, uuid
roles = [("clinician", "Clinician", "Sees and assesses their own notes."),
         ("supervisor", "Supervisor", "Sees their team's notes and reports."),
         ("qa", "QA", "Sees all notes and reports."),
         ("admin", "Admin", "Everything, plus rule sets and OpenEMR sync.")]
json.dump([{"allowedMemberTypes": ["User"], "value": v, "displayName": d, "description": t,
            "isEnabled": True, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "cnat-role-" + v))}
           for v, d, t in roles], open(sys.argv[1], "w"))
PY
    ENTRA_APP_ID="$(az ad app create --display-name "$ENTRA_APP_NAME" --sign-in-audience AzureADMyOrg \
      --web-redirect-uris "$host/.auth/login/aad/callback" --enable-id-token-issuance true \
      --app-roles "@$roles" --query appId -o tsv)"
    rm -f "$roles"
    save_state
  fi
  az ad sp show --id "$ENTRA_APP_ID" -o none 2>/dev/null || az ad sp create --id "$ENTRA_APP_ID" -o none

  say "Sign-in (Easy Auth) on $APP"
  local secret
  secret="$(az ad app credential reset --id "$ENTRA_APP_ID" --display-name easy-auth --years 2 --query password -o tsv 2>/dev/null)"
  az webapp config appsettings set -g "$RG" -n "$APP" --settings "MICROSOFT_PROVIDER_AUTHENTICATION_SECRET=$secret" -o none
  az webapp auth update -g "$RG" -n "$APP" --enabled true \
    --action RedirectToLoginPage --redirect-provider azureactivedirectory -o none
  az webapp auth microsoft update -g "$RG" -n "$APP" --yes \
    --client-id "$ENTRA_APP_ID" --client-secret-setting-name MICROSOFT_PROVIDER_AUTHENTICATION_SECRET \
    --issuer "https://login.microsoftonline.com/$tenant/v2.0" -o none

  say "Give you (the signed-in Azure user) the Admin role"
  local sp_id user_id role_id
  sp_id="$(az ad sp show --id "$ENTRA_APP_ID" --query id -o tsv)"
  user_id="$(az ad signed-in-user show --query id -o tsv)"
  role_id="$(az ad app show --id "$ENTRA_APP_ID" --query "appRoles[?value=='admin'].id | [0]" -o tsv)"
  az rest --method POST --uri "https://graph.microsoft.com/v1.0/users/$user_id/appRoleAssignments" \
    --body "{\"principalId\":\"$user_id\",\"resourceId\":\"$sp_id\",\"appRoleId\":\"$role_id\"}" -o none 2>/dev/null \
    || echo "   (already assigned)"
  echo "   Assign other people in Entra ID > Enterprise applications > $ENTRA_APP_NAME > Users and groups."
}

deploy() {
  az account show -o none || { echo "Run 'az login' first."; exit 1; }
  "$ROOT/deploy/package.sh" "$ROOT/build/cnat-app.zip"

  say "Deploy to $APP (the build on Azure takes a few minutes)"
  az webapp deploy -g "$RG" -n "$APP" --src-path "$ROOT/build/cnat-app.zip" --type zip --track-status false -o none
  info
}

info() {
  echo
  echo "Site:            https://$APP.azurewebsites.net"
  echo "Resource group:  $RG ($LOCATION)"
  echo "App Service:     $APP  (plan $PLAN, $PLAN_SKU)"
  echo "Azure SQL:       $SQL_SERVER.database.windows.net / $SQL_DB"
  echo "Logs:            az webapp log tail -g $RG -n $APP"
}

case "${1:-}" in
  setup) setup ;;
  deploy) deploy ;;
  info) info ;;
  *) echo "Usage: deploy/azure.sh setup|deploy|info"; exit 1 ;;
esac
