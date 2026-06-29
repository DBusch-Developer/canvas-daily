"""Layer 38 — AI course classifier.

Groq is mocked at the httpx transport boundary. Given each course's name and
whether it has assignments, the model returns a parallel list of booleans
(True = a real class, False = a Canvas extra). Failures and malformed responses
raise a clean AIError so the caller can fall back to showing everything. The API
key rides in the Authorization header and is never logged.
"""
import json
import httpx
import pytest

from app.ai import classify_courses, AIError, AITimeoutError


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _responder(bools):
    def handler(request):
        # never leak the key into assertions; just return the canned classification
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(bools)}}]
        })
    return handler


def test_classify_returns_parallel_booleans():
    courses = [
        {"name": "CSA250 Intro Artificial Intelligence (22255)", "has_assignments": True},
        {"name": "Lunch Brunch", "has_assignments": False},
        {"name": "English: Language Arts Companion", "has_assignments": True},
    ]
    out = classify_courses(courses, _client(_responder([True, False, True])), "k")
    assert out == [True, False, True]


def test_classify_empty_list_makes_no_call():
    def boom(request):  # must not be called
        raise AssertionError("no Groq call for an empty list")
    assert classify_courses([], _client(boom), "k") == []


def test_classify_timeout_raises_clean_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)
    with pytest.raises(AITimeoutError):
        classify_courses([{"name": "X", "has_assignments": True}], _client(handler), "k")


def test_classify_wrong_length_raises_aierror():
    courses = [{"name": "A", "has_assignments": True},
               {"name": "B", "has_assignments": False}]
    # model returns one bool for two courses → malformed
    with pytest.raises(AIError):
        classify_courses(courses, _client(_responder([True])), "k")


def test_classify_non_bool_payload_raises_aierror():
    with pytest.raises(AIError):
        classify_courses([{"name": "A", "has_assignments": True}],
                         _client(_responder(["yes"])), "k")
