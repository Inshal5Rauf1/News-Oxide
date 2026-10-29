"""Stub curate: writes a minimal curated JSON. CURATE_EXIT sets the exit code; CURATE_DEGRADED=1
writes grouped=false, probe_ok=false."""
import json, os, sys
date, sent = sys.argv[1], sys.argv[2]
bad = os.environ.get("CURATE_DEGRADED") == "1"
open(os.path.join(sent, date + "-curated.json"), "w").write(json.dumps(
    {"themes": [], "rest": [], "stats": {"grouped": not bad, "probe_ok": not bad,
                                         "no_model": "--no-model" in sys.argv}}))
print("curate: 0 items, 0 themes, grouped=1, probe=ok")
sys.exit(int(os.environ.get("CURATE_EXIT", "0")))
