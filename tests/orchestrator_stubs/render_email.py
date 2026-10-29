"""Stub renderer: writes a file for each subcommand; RENDER_FAIL (comma list of subcommands) exits 1."""
import os, sys
cmd, a = sys.argv[1], sys.argv[2:]
if cmd in os.environ.get("RENDER_FAIL", "").split(","):
    sys.exit(1)
if cmd == "curated":
    outs = a[2:4]
elif cmd == "eml":
    outs = [a[3]]
elif cmd == "heartbeat":
    outs = [a[1]]
else:
    outs = [a[2]]
for p in outs:
    open(p, "w").write("%s %s\n" % (cmd, " ".join(a)))
