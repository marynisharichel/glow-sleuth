import os
from pathlib import Path


def load_env(path) -> None:
    """Tiny .env reader: KEY=VALUE lines. Real environment variables win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _mask(k: str) -> str:
    return f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "****"


def _save_env(path: Path, key: str) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    lines = [l for l in lines if not l.strip().startswith("SERPER_API_KEY")]
    lines.insert(0, f"SERPER_API_KEY={key}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def setup_serper_key(root, code_key: str = "", cli_key: str | None = None,
                     interactive: bool | None = None, first_run: bool = True) -> bool:
    """Find the Serper key and (on the first run) prompt for it / test it.

    Priority: --key option > key pasted in app.py > system environment > .env file.
    first_run=False (Flask's reloader child process) only applies the key, silently.
    """
    import sys
    from .live import LiveSearchError, SerperShopping

    root = Path(root)
    pre = os.environ.get("SERPER_API_KEY")
    load_env(root / ".env")
    source = None
    if cli_key:
        os.environ["SERPER_API_KEY"], source = cli_key, "the --key option"
    elif code_key.strip():
        os.environ["SERPER_API_KEY"], source = code_key, "the SERPER_API_KEY line in app.py"
    elif pre:
        source = "a Windows/system environment variable (this overrides .env)"
    elif os.environ.get("SERPER_API_KEY"):
        source = "the .env file"
    if not first_run:
        return SerperShopping().enabled

    interactive = sys.stdin.isatty() if interactive is None else interactive
    live = SerperShopping(num=1)
    if not live.enabled and interactive:
        print("\nNo Serper API key found.")
        k = input("Paste your Serper key (or press Enter to skip and use demo data): ").strip()
        if k:
            os.environ["SERPER_API_KEY"], source = k, "what you typed just now"
            if input("Save it to .env for next time? (y/n): ").strip().lower().startswith("y"):
                _save_env(root / ".env", k)
                print("Saved to .env")
    for attempt in range(3):
        live = SerperShopping(num=1)
        if not live.enabled:
            print("Serper: no key, so the app will use DEMO data.")
            return False
        print(f"Serper key comes from {source}: {_mask(live.key)} ({len(live.key)} characters)")
        try:
            live._post({"q": "serum", "gl": "in", "hl": "en", "num": 1})
            print("Serper accepted the key. Live search is ON.")
            return True
        except LiveSearchError as e:
            print(f"Serper rejected the key: {e}")
            if not interactive or attempt == 2:
                print("Fix: copy a fresh key from serper.dev > API Keys, or run: python app.py --key YOUR_KEY")
                return False
            k = input("Paste a different key (or press Enter to continue without live search): ").strip()
            if not k:
                return False
            os.environ["SERPER_API_KEY"], source = k, "what you typed just now"
            if input("Save it to .env? (y/n): ").strip().lower().startswith("y"):
                _save_env(root / ".env", k)
    return False
