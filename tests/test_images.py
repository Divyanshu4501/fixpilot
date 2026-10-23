"""Image pulls: a broken download must raise (and be retried), not pass silently."""

from types import SimpleNamespace

import pytest
from docker.errors import ImageNotFound

from fixpilot.images import ensure_image, image_for


class FakeDocker:
    """Pull streams are scripted per attempt; the image 'exists' after a clean pull."""

    def __init__(self, streams: list[list[dict]], local: bool = False):
        self.streams, self.local, self.pull_calls = streams, local, 0
        self.api = SimpleNamespace(pull=self._pull)
        self.images = SimpleNamespace(get=self._get)

    def _pull(self, repo, tag, stream, decode):
        events = self.streams[self.pull_calls]
        self.pull_calls += 1
        if not any("error" in e for e in events):
            self.local = True
        return iter(events)

    def _get(self, image):
        if not self.local:
            raise ImageNotFound("no such image")


IMAGE = "swebench/sweb.eval.x86_64.x_1776_x-1:latest"
BROKEN = [{"status": "Downloading"}, {"error": "unexpected EOF"}]
CLEAN = [{"status": "Downloading"}, {"status": "Pull complete"}]


def test_image_name_rule():
    assert image_for("django__django-11099") == "swebench/sweb.eval.x86_64.django_1776_django-11099:latest"


def test_already_local_image_is_not_pulled_and_not_ours_to_remove():
    client = FakeDocker([], local=True)
    assert ensure_image(client, IMAGE) is False and client.pull_calls == 0


def test_broken_pull_is_retried_then_succeeds():
    client = FakeDocker([BROKEN, CLEAN])
    assert ensure_image(client, IMAGE, attempts=3, wait_s=0) is True
    assert client.pull_calls == 2


def test_pull_that_keeps_failing_raises():
    client = FakeDocker([BROKEN, BROKEN, BROKEN])
    with pytest.raises(RuntimeError, match="unexpected EOF"):
        ensure_image(client, IMAGE, attempts=3, wait_s=0)
    assert client.pull_calls == 3
