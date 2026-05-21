"""
login.py — Run this ONCE daily (or when access token expires) to generate
a fresh Kite access token. Copy the printed token into your Railway env var.

Usage:
  python login.py

It will open the Kite login URL and ask you to paste the request token
from the redirect URL.
"""

import os
import webbrowser
from kiteconnect import KiteConnect

API_KEY    = os.environ.get("KITE_API_KEY", input("Enter API key: ").strip())
API_SECRET = os.environ.get("KITE_API_SECRET", input("Enter API secret: ").strip())

kite = KiteConnect(api_key=API_KEY)
login_url = kite.login_url()

print(f"\nOpening login URL:\n{login_url}\n")
webbrowser.open(login_url)

print("After logging in, Kite redirects to your callback URL like:")
print("  https://your-redirect.com?request_token=XXXXXX&action=login&status=success")
print()
request_token = input("Paste the request_token from the URL: ").strip()

data = kite.generate_session(request_token, api_secret=API_SECRET)
access_token = data["access_token"]

print(f"\n✅ Access token generated:")
print(f"   KITE_ACCESS_TOKEN={access_token}")
print("\nSet this in your Railway environment variables.")
print("It expires at midnight — re-run this script each trading day.")
