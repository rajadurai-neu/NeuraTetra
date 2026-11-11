"""Make a CNAT_USERS entry for the built-in sign-in. The password is typed, never echoed or stored.

  .venv/bin/python api/scripts/hash_password.py
"""
import getpass
import json
import secrets

from cnat_api.web.password_auth import hash_password

ROLES = ("clinician", "supervisor", "qa", "admin")

username = input("Username (e.g. jsmith): ").strip().lower()
name = input("Full name as used on notes (e.g. Jane Smith): ").strip()
roles = [r.strip().lower() for r in input(f"Roles, comma separated {ROLES}: ").split(",") if r.strip()]
bad = [r for r in roles if r not in ROLES]
if not username or not name or not roles or bad:
    raise SystemExit(f"Need a username, a name and roles from {ROLES}." + (f" Unknown: {bad}" if bad else ""))
pw = getpass.getpass("Password (min 12 characters): ")
if len(pw) < 12 or pw != getpass.getpass("Again: "):
    raise SystemExit("Passwords must match and be at least 12 characters.")

print("\nAdd this object to the CNAT_USERS JSON list:\n")
print(json.dumps({"username": username, "name": name, "roles": roles, "password_hash": hash_password(pw)}))
print("\nIf you don't have CNAT_SESSION_SECRET yet, you can use:\n" + secrets.token_urlsafe(48))
