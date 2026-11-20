import argparse
import json
import os
import traceback
from pathlib import Path

from flask import Flask, Response, jsonify, request

from glow_sleuth.agent import GlowSleuth
from glow_sleuth.config import setup_serper_key
from glow_sleuth.live import SerperShopping
from glow_sleuth.scan import ScanError, scan
from glow_sleuth.sources import JsonCatalogSource

ROOT = Path(__file__).parent

# ---------------------------------------------------------------------------
# SERPER KEY - pick ONE way (priority: --key  >  this line  >  system env  >  .env)
#   A) paste it between the quotes:   SERPER_API_KEY = "abc123..."
#   B) run:  python app.py --key abc123...
#   C) put  SERPER_API_KEY=abc123...  in the .env file
#   D) leave everything empty: the terminal will ASK you for it when the server starts
# ---------------------------------------------------------------------------
SERPER_API_KEY = ""

_ap = argparse.ArgumentParser(add_help=False)
_ap.add_argument("--key")
_cli, _ = _ap.parse_known_args()
# Flask's reloader runs this file twice; only the first (parent) process prompts and tests the key
setup_serper_key(ROOT, SERPER_API_KEY, _cli.key, first_run=os.environ.get("WERKZEUG_RUN_MAIN") != "true")
source = JsonCatalogSource(ROOT / "data")            # reads every data/*.json file
sleuth = GlowSleuth([source], live=SerperShopping())
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024


@app.get("/")
def index():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    catalog = json.dumps(source.raw(), ensure_ascii=False).replace("</", "<\\/")
    return Response(html.replace("__CATALOG_JSON__", catalog), mimetype="text/html")


@app.post("/api/recommend")
def api_recommend():
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify(code="empty_query"), 400
    prefs, trace, recs, status = sleuth.search(
        query, platforms=body.get("platforms") or None, profile=body.get("profile") or {}
    )
    return jsonify(
        summary=prefs.describe(), trace=trace, count=len(recs), budget=prefs.budget,
        data=status["mode"], error=status["error"], recs=[r.to_dict() for r in recs],
    )


@app.post("/api/scan")
def api_scan():
    files = request.files.getlist("images")
    try:
        profile = json.loads(request.form.get("profile") or "{}")
    except ValueError:
        profile = {}
    try:
        result = scan([f.read() for f in files], request.form.get("note", ""), profile, sleuth.catalog())
        return jsonify(result)
    except ScanError as e:
        print(f"[scan] FAILED with code: {e.code}")
        return jsonify(code=e.code), e.status
    except Exception:
        traceback.print_exc()          # shows the real bug in the terminal
        return jsonify(code="ai_error"), 500


@app.errorhandler(413)
def too_big(_):
    return jsonify(code="too_large"), 413


if __name__ == "__main__":
    app.run(debug=True, port=5000)
  