"""Stub curate: writes a minimal curated JSON. CURATE_EXIT sets the exit code; CURATE_DEGRADED=1
writes grouped=false, probe_ok=false. CURATE_THEMES=failed|skipped writes probe_ok=true, grouped=false
and themes_skipped accordingly. items_in = number of source files present."""
import json, os, sys
date, sent = sys.argv[1], sys.argv[2]
KEYS = "trending huggingface hackernews newrepos hnbest feeds reddit".split()
n = sum(os.path.exists(os.path.join(sent, "%s-%s.json" % (date, k))) for k in KEYS)
bad = os.environ.get("CURATE_DEGRADED") == "1"
th = os.environ.get("CURATE_THEMES", "")
open(os.path.join(sent, date + "-curated.json"), "w").write(json.dumps(
    {"themes": [], "rest": [], "stats": {"items_in": n, "grouped": not bad and not th, "probe_ok": not bad,
                                         "themes_skipped": th == "skipped",
                                         "no_model": "--no-model" in sys.argv}}))
print("curate: 0 items, 0 themes, grouped=1, probe=ok")
sys.exit(int(os.environ.get("CURATE_EXIT", "0")))
