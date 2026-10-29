import os, subprocess


def test_orchestrator():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_orchestrator.sh")
    r = subprocess.run(["bash", path], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
