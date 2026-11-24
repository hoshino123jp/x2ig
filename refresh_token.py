"""Instagramの長期アクセストークン（有効期限60日）を更新し、新しいトークンを標準出力に出す。"""
import os
import sys

import requests

r = requests.get(
    "https://graph.instagram.com/refresh_access_token",
    params={"grant_type": "ig_refresh_token", "access_token": os.environ["IG_ACCESS_TOKEN"]},
    timeout=60,
)
if r.status_code != 200:
    print(f"トークン更新に失敗: {r.status_code} {r.text[:300]}", file=sys.stderr)
    sys.exit(1)
data = r.json()
print(f"更新成功。有効期限まで約{data.get('expires_in', 0) // 86400}日", file=sys.stderr)
print(data["access_token"])
