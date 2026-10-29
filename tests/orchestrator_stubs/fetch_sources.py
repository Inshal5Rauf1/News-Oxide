"""Stub fetcher for tests/test_orchestrator.sh: fixtures to stdout, FAIL_KEYS -> [] exit 3."""
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
cmd = sys.argv[1]
if cmd == "commit-state":
    open(os.path.join(here, "tmp", "committed-" + sys.argv[2]), "a").write("x\n")
    sys.exit(0)
if cmd in os.environ.get("FAIL_KEYS", "").split(","):
    print("[]"); sys.exit(3)
if cmd == "releases":
    print('[{"repo": "o/r", "tag": "v1"}]' if os.environ.get("RELEASES_ITEMS") else "[]")
    sys.exit(0)
sys.stdout.write(open(os.path.join(os.environ["FIXTURES"], cmd + ".json")).read())
