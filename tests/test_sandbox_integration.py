"""Integration tests: need Docker and the 11099 image. Run with `uv run pytest -m integration`."""

import pytest

from fixpilot.sandbox import Sandbox

IMAGE = "swebench/sweb.eval.x86_64.django_1776_django-11099:latest"

pytestmark = pytest.mark.integration


def test_sandbox_runs_times_out_diffs_and_has_no_network():
    with Sandbox(IMAGE) as sb:
        assert sb.run("pwd").output.strip() == "/testbed"
        assert sb.run("sleep 10", timeout=2).timed_out
        assert "no-net" in sb.run("getent hosts github.com || echo no-net").output
        assert sb.run("echo $GIT_PAGER").output.strip() == "cat"
        sb.run("echo '# edit' >> django/contrib/auth/validators.py")
        assert "+# edit" in sb.diff()
        sb.run("echo 'x' > untracked_repro.py")
        assert "untracked_repro" not in sb.diff()
    assert sb.container is None
