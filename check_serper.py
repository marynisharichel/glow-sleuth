"""Run:  python check_serper.py
Tests your Serper key WITHOUT any of the app's own logic, so you can see whether the key itself is accepted."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from glow_sleuth.config import load_env
from glow_sleuth.live import BASE, SerperShopping

root = Path(__file__).parent
env = root / ".env"
print(f"1) .env file: {'FOUND' if env.exists() else 'NOT FOUND'} ({env})")
if not env.exists() and (root / ".env.txt").exists():
    print("   !! You have '.env.txt'. Rename it to exactly '.env' (Explorer > View > File name extensions).")
had_system = bool(os.environ.get("SERPER_API_KEY"))
load_env(env)
key = SerperShopping().key
if not key:
    raise SystemExit("2) No key found. Add  SERPER_API_KEY=your-key  to .env, or run: python check_serper.py")
if "paste-your" in key:
    raise SystemExit("2) .env still has the placeholder text. Put your real key there.")
print(f"2) Key: {key[:4]}...{key[-4:]}  length={len(key)}  from {'SYSTEM ENVIRONMENT (overrides .env!)' if had_system else '.env'}")
print("   Compare the first 4 / last 4 characters and the length with the key on serper.dev > API Keys.")


def call(path, payload):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"X-API-KEY": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return None, str(e)


for path, label in (("/search", "web search"), ("/shopping", "shopping (what Glow Sleuth uses)")):
    code, body = call(path, {"q": "serum for dry skin", "gl": "in", "num": 3})
    ok = code == 200
    print(f"3) {label:34s} -> HTTP {code}  {'OK' if ok else body}")
    if ok and path == "/shopping":
        items = body.get("shopping") or []
        print(f"   {len(items)} shopping results, e.g. {[i.get('source') for i in items[:3]]}")

print("""
How to read this:
 - both OK              -> key is fine. Restart: python app.py
 - both 'Unauthorized'  -> Serper does not accept this key. Create a NEW key on serper.dev > API Keys,
                           copy it with the copy button, and use it. Also confirm you are on serper.dev
                           (not serpapi.com) and that your email is verified / you have credits.
 - search OK, shopping 'Unauthorized' -> your plan can't use shopping; tell me and I'll switch the app to another method.
 - Could not connect    -> internet / firewall / VPN is blocking google.serper.dev.""")
