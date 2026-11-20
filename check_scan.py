import json
import sys

from glow_sleuth.scan import ScanError, _candidate_models, _env, scan

gem = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
ser = _env("SERPER_API_KEY")
print("1) GEMINI_API_KEY:", "found, length " + str(len(gem)) if gem else "MISSING in .env")
print("2) SERPER_API_KEY:", "found, length " + str(len(ser)) if ser else "MISSING in .env")
if not gem:
    raise SystemExit("Add GEMINI_API_KEY=your-key to .env and run again.")

try:
    models = _candidate_models(gem)
    print("3) Models your key can use:", models[:6])
except ScanError as e:
    raise SystemExit(f"3) FAILED ({e.code}). Read the [scan] line above for the reason.")

if len(sys.argv) < 2:
    raise SystemExit("4) Run it with a photo: python check_scan.py photo.jpg")

with open(sys.argv[1], "rb") as f:
    img = f.read()
print(f"4) Scanning {sys.argv[1]} ({len(img) // 1024} KB) ...")
try:
    result = scan([img], "", {}, [])
except ScanError as e:
    raise SystemExit(f"FAILED with code '{e.code}'. Read the [scan] lines above for the reason.")
print(json.dumps(result, indent=2, ensure_ascii=False))