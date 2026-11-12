"""Print the App Service settings for OpenEMR intake, from your local .env and key file.

Run it YOURSELF after openemr_register.py, and paste the output into
App Service > Settings > Environment variables > App settings (Advanced edit takes JSON).
    python api/scripts/openemr_azure_settings.py > openemr-azure.secret.txt

The output contains the client secret and private key: don't commit it, delete it after pasting.
(*.secret.txt is gitignored.)
"""
from __future__ import annotations

import json
import sys

from cnat_api.config import OpenEMRSettings


def main() -> int:
    s = OpenEMRSettings()
    if not s.client_id:
        print("OPENEMR_CLIENT_ID is empty. Run api/scripts/openemr_register.py first.", file=sys.stderr)
        return 1
    pem = s.private_key_bytes().decode().strip()
    values = {
        "OPENEMR_BASE_URL": s.base_url,
        "OPENEMR_SITE": s.site,
        "OPENEMR_CLIENT_ID": s.client_id,
        "OPENEMR_CLIENT_SECRET": s.client_secret,
        "OPENEMR_KEY_ID": s.key_id,
        "OPENEMR_PRIVATE_KEY": pem.replace("\n", "\\n"),
        "OPENEMR_SCOPES": s.scopes,
        "OPENEMR_ALLOW_INSECURE_HTTP": "1" if s.allow_insecure_http else "0",
        "OPENEMR_SYNC_MINUTES": str(s.sync_minutes or 5),
        "OPENEMR_LOOKBACK_DAYS": str(s.lookback_days),
        "OPENEMR_DEFAULT_PROGRAM": s.default_program or "Adult Outpatient",
    }
    print(json.dumps([{"name": k, "value": v, "slotSetting": False} for k, v in values.items()], indent=2))
    print("\nPaste the JSON into App Settings > Advanced edit (merge with what's there), then Apply.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
