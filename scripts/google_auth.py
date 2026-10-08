# scripts/google_auth.py — one-time Google sign-in for Calendar + Tasks sync. Run it yourself: it opens a browser for consent.
#
#   .venv/Scripts/python.exe scripts/google_auth.py path/to/client_secret.json
#
# The OAuth client must be of type "Desktop app" (Google Cloud console -> APIs & Services -> Credentials). On success it
# appends GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN to .env (never printed) and tells you which GitHub
# secrets to add. The app must be "In production" or the refresh token stops working after 7 days.
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from gcal.google_client import SCOPES  # noqa: E402

OURS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: .venv/Scripts/python.exe scripts/google_auth.py <path to the downloaded client_secret JSON>  (exactly one argument)")
        return 2
    secret_file = Path(sys.argv[1])
    client = json.loads(secret_file.read_text(encoding="utf-8"))
    client = client.get("installed") or client.get("web") or {}
    if not client.get("client_id"):
        print("That file has no client id. Download the OAuth client JSON of type 'Desktop app'.")
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not creds.refresh_token:
        print("Google returned no refresh token. Remove the app's access at myaccount.google.com/permissions and run this again.")
        return 1

    env = ROOT / ".env"
    existing = env.read_text(encoding="utf-8") if env.exists() else ""
    lines = [line for line in existing.splitlines() if not line.startswith(OURS)]
    lines += [f"GOOGLE_CLIENT_ID={client['client_id']}", f"GOOGLE_CLIENT_SECRET={client['client_secret']}",
              f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}"]
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Saved to .env. Add the same three values as GitHub Actions secrets: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
          "GOOGLE_REFRESH_TOKEN (copy them from .env; do not paste them anywhere else).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
