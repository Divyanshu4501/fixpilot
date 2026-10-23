"""No leakage: hidden dataset fields never reach the agent's prompts, tools or sandbox."""

import json

import pytest

from fixpilot.agent import AgentConfig, run_agent
from fixpilot.tasks import FORBIDDEN_FIELDS, VISIBLE_FIELDS, assert_visible_only, visible_task
from tests.fakes import FakeClient, FakeSandbox, bash_call, response, submit_call

# A dataset row with unmistakable secret values in every hidden field.
ROW = {
    "instance_id": "astropy__astropy-1", "repo": "astropy/astropy", "base_commit": "deadbeef",
    "problem_statement": "Units are converted wrongly.", "image": "img:latest",
    "patch": "diff --git a/SECRET_GOLD_PATCH.py b/SECRET_GOLD_PATCH.py",
    "test_patch": "diff --git a/tests/SECRET_TEST_PATCH.py",
    "FAIL_TO_PASS": ["test_SECRET_FAIL_TO_PASS"],
    "PASS_TO_PASS": ["test_SECRET_PASS_TO_PASS"],
    "hints_text": "SECRET_HINT: look at units.py",
    "eval_script": "SECRET_EVAL_SCRIPT",
    "difficulty": "SECRET_DIFFICULTY",
    "environment_setup_commit": "SECRET_ENV_COMMIT",
}
SECRETS = ["SECRET_GOLD_PATCH", "SECRET_TEST_PATCH", "SECRET_FAIL_TO_PASS", "SECRET_PASS_TO_PASS",
           "SECRET_HINT", "SECRET_EVAL_SCRIPT", "SECRET_DIFFICULTY", "SECRET_ENV_COMMIT"]


def test_visible_task_keeps_only_visible_fields():
    task = visible_task(ROW)
    assert set(task) == set(VISIBLE_FIELDS)
    assert not any(s in json.dumps(task) for s in SECRETS)


def test_every_forbidden_field_is_dropped():
    task = visible_task(ROW)
    for field in FORBIDDEN_FIELDS:
        assert field not in task


def test_run_agent_refuses_a_task_with_hidden_fields():
    with pytest.raises(ValueError, match="non-visible"):
        run_agent(ROW, FakeSandbox(), AgentConfig(verbose=False), client=FakeClient([]))
    with pytest.raises(ValueError):
        assert_visible_only({**visible_task(ROW), "patch": "x"})


def test_nothing_secret_reaches_the_model_or_the_sandbox():
    client = FakeClient([response(bash_call("grep -rn units .")), response(submit_call())])
    sb = FakeSandbox(output="units.py:1: def convert()")
    run_agent(visible_task(ROW), sb, AgentConfig(verbose=False), client=client)
    sent = json.dumps(client.requests)  # instructions + input history + tools, every call
    for secret in SECRETS:
        assert secret not in sent, secret
        assert not any(secret in c for c in sb.commands), secret
    assert "Units are converted wrongly." in sent  # the issue itself does get through
