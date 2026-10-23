"""Agent loop: trimming, cost, limits, nudges, submit, history replay."""

import pytest

from fixpilot.agent import NUDGE, PRICES, AgentConfig, format_observation, run_agent, step_cost
from fixpilot.sandbox import CommandResult
from tests.fakes import (FakeClient, FakeItem, FakeSandbox, bash_call, response, submit_call,
                         text_message, usage)

TASK = {"instance_id": "repo__repo-1", "repo": "o/repo", "base_commit": "abc",
        "problem_statement": "Something is broken.", "image": "img:latest"}


def config(**kw) -> AgentConfig:
    return AgentConfig(model="gpt-6-luna", verbose=False, **kw)


# ---------- output trimming ----------

def test_short_output_is_unchanged():
    obs = format_observation(CommandResult("hello\n", 0, 0.1), max_chars=100)
    assert obs == "exit code: 0\noutput:\nhello\n"


def test_long_output_keeps_first_and_last_half():
    out = "A" * 6000 + "B" * 6000
    obs = format_observation(CommandResult(out, 0, 0.1), max_chars=10_000)
    body = obs.split("output:\n", 1)[1]
    assert body.startswith("A" * 5000) and body.endswith("B" * 5000)
    assert "[... 2,000 characters cut ...]" in body
    assert body.count("A") == 5000 and body.count("B") == 5000


def test_timeout_and_empty_output_are_labelled():
    obs = format_observation(CommandResult("", 124, 3.0), max_chars=100)
    assert "timed out" in obs and "(no output)" in obs


# ---------- cost ----------

def test_cost_uses_cached_and_uncached_rates():
    p_in, p_cached, p_out = PRICES["gpt-6-luna"]
    u = step_cost("gpt-6-luna", usage(tokens_in=10_000, cached=4_000, tokens_out=2_000, reasoning=500))
    expected = (6_000 * p_in + 4_000 * p_cached + 2_000 * p_out) / 1_000_000
    assert u.cost_usd == pytest.approx(expected)
    assert (u.tokens_in, u.tokens_cached, u.tokens_out, u.tokens_reasoning) == (10_000, 4_000, 2_000, 500)


def test_unknown_model_is_refused():
    with pytest.raises(ValueError, match="No prices"):
        step_cost("mystery-model", usage())


def test_missing_usage_is_refused():
    with pytest.raises(RuntimeError):
        step_cost("gpt-6-luna", None)


# ---------- the loop ----------

def test_submit_ends_the_run_and_patch_is_taken():
    client = FakeClient([response(bash_call("ls", "c1")), response(submit_call("s1"))])
    sb = FakeSandbox(patch="PATCH")
    r = run_agent(TASK, sb, config(), client=client)
    assert r.exit_reason == "submitted" and r.steps == 2 and r.patch == "PATCH"
    assert sb.commands == ["ls"]
    assert [s["tool"] for s in r.trajectory] == ["bash", "submit"]


def test_step_limit_stops_the_loop_and_still_takes_the_patch():
    client = FakeClient([response(bash_call("ls"))])  # bash forever
    sb = FakeSandbox(patch="PARTIAL")
    r = run_agent(TASK, sb, config(max_steps=3), client=client)
    assert r.exit_reason == "step_limit" and r.steps == 3 and len(client.requests) == 3
    assert r.patch == "PARTIAL"


def test_cost_limit_stops_the_loop():
    expensive = usage(tokens_in=1_000_000, tokens_out=0)  # 1M uncached tokens = $0.125 each
    client = FakeClient([response(bash_call("ls"), use=expensive)])
    r = run_agent(TASK, FakeSandbox(), config(max_cost_usd=0.30), client=client)
    assert r.exit_reason == "cost_limit" and r.steps == 3  # 0.125, 0.25, 0.375 >= 0.30 -> stop
    assert r.cost_usd == pytest.approx(0.375)


def test_no_tool_call_gets_a_nudge_and_counts_as_a_step():
    client = FakeClient([response(text_message("I think the bug is in x.py")), response(submit_call())])
    r = run_agent(TASK, FakeSandbox(), config(), client=client)
    assert r.exit_reason == "submitted" and r.steps == 2
    assert r.trajectory[0]["thought"] == "I think the bug is in x.py"
    second_input = client.requests[1]["input"]
    assert second_input[-1] == {"role": "user", "content": NUDGE}


def test_incomplete_response_answers_its_call_and_nudges():
    client = FakeClient([response(bash_call("ls", "c9"), status="incomplete"), response(submit_call())])
    sb = FakeSandbox()
    run_agent(TASK, sb, config(), client=client)
    assert sb.commands == []  # a cut-off call is never executed
    second_input = client.requests[1]["input"]
    outputs = [i for i in second_input if i.get("type") == "function_call_output"]
    assert outputs and outputs[0]["call_id"] == "c9"
    assert second_input[-1]["content"] == NUDGE


def test_history_replays_every_output_item_and_call_outputs():
    reasoning = FakeItem(type="reasoning", id="r1", encrypted_content="ENC", summary=[])
    client = FakeClient([response(reasoning, bash_call("ls", "c1")), response(submit_call())])
    run_agent(TASK, FakeSandbox(output="file.py"), config(), client=client)
    second = client.requests[1]
    assert second["store"] is False and second["parallel_tool_calls"] is False
    types = [i.get("type", i.get("role")) for i in second["input"]]
    assert types == ["user", "reasoning", "function_call", "function_call_output"]
    assert second["input"][1]["encrypted_content"] == "ENC"
    assert "file.py" in second["input"][3]["output"]
    # the system prompt is sent as `instructions`, identical every call (cache-friendly)
    assert client.requests[0]["instructions"] == second["instructions"]


def test_api_error_ends_with_error_and_keeps_patch():
    class Boom(FakeClient):
        def create(self, **kwargs):
            raise ConnectionError("network down")

    r = run_agent(TASK, FakeSandbox(patch="P"), config(), client=Boom([]))
    assert r.exit_reason == "error" and "network down" in r.error and r.patch == "P"
