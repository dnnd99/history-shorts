#!/usr/bin/env python3
"""Jalankan SEKALI di laptop (ada browser), bukan di VPS.
Butuh client_secret.json (OAuth client tipe Desktop) di folder ini.
Hasilnya token.json -> salin ke folder proyek di VPS."""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)
open("token.json", "w").write(creds.to_json())
print("token.json dibuat")
