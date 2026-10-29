#!/usr/bin/env python3
"""
claude_cli.py: run the Claude Code CLI headless (`claude -p`) on the owner's subscription.
No API key is used or passed; the subprocess sees only HOME and PATH.
"""
import json, os, re, subprocess, sys

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/home/inshal-rauf/.local/bin/claude")


def _fail(reason, stderr=""):
    tail = " ".join((stderr or "").split())[:200]
    print(f"claude_cli: {reason}" + (f": {tail}" if tail else ""), file=sys.stderr)


def parse_result(stdout):
    """Return the `type: result` object from a JSON object or array of events."""
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    for ev in data if isinstance(data, list) else [data]:
        if isinstance(ev, dict) and ev.get("type") == "result":
            return ev
    return None


def parse_json_text(text):
    """Parse JSON from model text, tolerating code fences and surrounding prose."""
    t = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "")
    for cand in (t, t[min([i for i in (t.find("{"), t.find("[")) if i >= 0], default=0):
                 max(t.rfind("}"), t.rfind("]")) + 1]):
        try:
            return json.loads(cand)
        except ValueError:
            pass
    return None


def ask(model, prompt, timeout, schema=None, runner=subprocess.run):
    """One headless call. Returns str (no schema), parsed JSON (schema), or None on any failure."""
    argv = [CLAUDE_BIN, "-p", prompt, "--model", model, "--output-format", "json",
            "--no-session-persistence", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}']
    if schema is not None:
        argv += ["--json-schema", json.dumps(schema)]
    env = {"HOME": os.environ["HOME"], "PATH": os.environ.get("PATH", ""), "TERM": "dumb"}
    try:
        p = runner(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        _fail(f"timeout after {timeout}s")
        return None
    except OSError as e:
        _fail(f"cannot run {CLAUDE_BIN}: {e}")
        return None
    if p.returncode != 0:
        _fail(f"exit {p.returncode}", p.stderr)
        return None
    res = parse_result(p.stdout)
    if res is None or res.get("is_error"):
        _fail("error result" if res else "unparseable output", p.stderr)
        return None
    if schema is None:
        return res.get("result")
    out = res.get("structured_output")
    if out is None:
        out = parse_json_text(res.get("result"))
    if out is None:
        _fail("no JSON in result", p.stderr)
    return out


def probe(timeout=60):
    """True if the CLI answers a trivial prompt (subscription login works)."""
    out = ask("haiku", "Reply with exactly: OK", timeout)
    return isinstance(out, str) and "OK" in out


if __name__ == "__main__":
    sys.exit(0 if probe() else 1)
