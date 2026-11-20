# Glow Sleuth ✨

Explainable beauty-product chatbot (Python + Flask). Tell it your skin type, product and budget,
or add a photo of a product to check it. It pools **review ratings from every shopping platform
in your catalog** and shows **all** matching products, best-reviewed first.

## Run in VS Code
```
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

## Live reviews with Serper.dev (recommended)
Ways to give the key (priority top to bottom): `python app.py --key YOUR_KEY` > the `SERPER_API_KEY = ""` line at the top
of `app.py` > a Windows/system environment variable > the `.env` file. If none is set, the terminal asks for it when
the server starts. At startup the app prints where the key came from and whether Serper accepted it
(this uses 1 credit per start).

1. Get a key at https://serper.dev
2. Copy `.env.example` to `.env` and paste the key: `SERPER_API_KEY=...`
3. Test it: `python check_serper.py` (shows exactly what's wrong if anything is)
4. Restart `python app.py` (the server does NOT reload `.env` by itself)

Each question then searches Google Shopping (India) live. Serper returns every shop's price, star rating and
review count; the same product listed on several shops is grouped, and the usual review-based ranking runs on top.
Shop names come from the results themselves (Amazon, Nykaa, Purplle, Flipkart, Tira, Myntra, Sephora, brand sites...),
so there is no platform limit. Without a key the app uses the fictional demo catalog in `data/`. With a key, a Serper failure shows the error instead of fake data.
Listings with no reviews are skipped. Live results don't include skin-type data: skin type comes from your search
and from words in the product title, so check the label for sensitive skin.

## Chats
Saved in your browser (localStorage): new chat, search across all chats, delete one / all, copy or edit your own
messages (editing re-sends the question and replaces everything after it).

## How ranking works
* Every product that matches category + skin type + budget is returned (add "top 5" to the query to limit).
* Review score = Bayesian average of ratings pooled across all platforms, weighted by review count,
  so 4.8★ from 12 reviews doesn't beat 4.4★ from 12,000.
* Platforms are never hard-coded. Any platform name in the data appears as a filter chip in the UI.

## Adding platforms / real reviews
`data/*.json` - every file in this folder is loaded and merged (same product `id` => offers are combined).
```json
{"id":"brand-serum","brand":"...","name":"...","category":"serum","size":"30 ml",
 "skin_types":["dry","normal"],"concerns":["hydration"],"ingredients":["Hyaluronic acid"],
 "highlights":{"pros":["..."],"cons":["..."]},
 "offers":[{"platform":"Nykaa","price":599,"rating":4.5,"reviews":8200,"url":"https://..."},
           {"platform":"Tira","price":579,"rating":4.3,"reviews":300}]}
```
The bundled catalog is **demo data** (fictional brands, made-up ratings). Replace it with real data
from your own scraper, a platform affiliate API or a licensed feed. For automatic feeds, subclass
`Source` in `glow_sleuth/sources.py` and add it to `GlowSleuth([...])` in `app.py`.
Check each platform's terms of service before scraping.

## Photo check (camera icon)
Needs an Anthropic API key. Easiest: put `ANTHROPIC_API_KEY=sk-ant-...` in `.env`. Or set it BEFORE `python app.py`:
- Windows PowerShell: `$env:ANTHROPIC_API_KEY="sk-ant-..."`
- Windows cmd: `set ANTHROPIC_API_KEY=sk-ant-...`
- Mac/Linux: `export ANTHROPIC_API_KEY=sk-ant-...`

Optional: `GLOW_MODEL` overrides the Claude model used.

## Terminal version
```
python main.py "serum for dry skin under 1000"
python main.py "serum under 1000" --top 3
```
