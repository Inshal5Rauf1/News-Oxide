import json
import subprocess

import claude_cli


def _cp(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


def _result(**kw):
    return json.dumps([{"type": "system", "subtype": "init"}, dict(type="result", is_error=False, **kw)])


def _runner(cp, calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append((argv, kw))
        if isinstance(cp, Exception):
            raise cp
        return cp
    return run


def test_ask_argv_has_no_tools_and_no_mcp():
    calls = []
    claude_cli.ask("haiku", "hi", 5, runner=_runner(_cp(_result(result="x")), calls))
    argv, kw = calls[0]
    assert argv[1:3] == ["-p", "hi"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--no-session-persistence" in argv and "--json-schema" not in argv
    assert set(kw["env"]) <= {"HOME", "PATH", "TERM"}
    assert kw["timeout"] == 5


def test_ask_returns_result_string():
    assert claude_cli.ask("haiku", "hi", 5, runner=_runner(_cp(_result(result="hello")))) == "hello"


def test_ask_returns_structured_output_with_schema():
    calls = []
    out = claude_cli.ask("haiku", "hi", 5, schema={"type": "object"},
                         runner=_runner(_cp(_result(result="", structured_output={"w": "blue"})), calls))
    assert out == {"w": "blue"}
    assert "--json-schema" in calls[0][0]


def test_ask_schema_falls_back_to_result_text():
    out = claude_cli.ask("haiku", "hi", 5, schema={"type": "object"},
                         runner=_runner(_cp(_result(result='```json\n{"w": "blue"}\n```'))))
    assert out == {"w": "blue"}


def test_ask_none_on_nonzero_exit(capsys):
    assert claude_cli.ask("haiku", "hi", 5, runner=_runner(_cp("", 1, "boom\n  bad"))) is None
    err = capsys.readouterr().err
    assert err.startswith("claude_cli: ") and err.count("\n") == 1 and "boom bad" in err


def test_ask_none_on_timeout(capsys):
    r = _runner(subprocess.TimeoutExpired("claude", 5))
    assert claude_cli.ask("haiku", "hi", 5, runner=r) is None
    assert capsys.readouterr().err.startswith("claude_cli: ")


def test_ask_none_on_is_error_and_garbage(capsys):
    bad = json.dumps({"type": "result", "is_error": True, "result": "nope"})
    assert claude_cli.ask("haiku", "hi", 5, runner=_runner(_cp(bad))) is None
    assert claude_cli.ask("haiku", "hi", 5, runner=_runner(_cp("not json"))) is None
    assert capsys.readouterr().err.count("claude_cli: ") == 2


def test_parse_result_array_form():
    assert claude_cli.parse_result(_result(result="a"))["result"] == "a"


def test_parse_result_single_object():
    assert claude_cli.parse_result('{"type":"result","result":"a"}')["result"] == "a"
    assert claude_cli.parse_result('[{"type":"system"}]') is None


def test_parse_result_strips_fences():
    assert claude_cli.parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert claude_cli.parse_json_text('Sure! Here:\n[1, 2]\nDone.') == [1, 2]
    assert claude_cli.parse_json_text("no json") is None


def test_probe_true_on_ok(monkeypatch):
    monkeypatch.setattr(claude_cli, "ask", lambda m, p, t, **k: "OK")
    assert claude_cli.probe() is True
    monkeypatch.setattr(claude_cli, "ask", lambda m, p, t, **k: None)
    assert claude_cli.probe() is False
