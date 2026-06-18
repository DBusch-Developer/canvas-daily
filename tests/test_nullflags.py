"""Layer 16 - guard null submission flags.

Canvas can return a submission with `late`, `missing`, or `excused` as explicit
JSON `null` (not just absent). `_parse` used `submission.get(key, False)`, which
only defaults when the key is missing — a present `null` passed `None` straight
through, and those assignment columns are NOT NULL, so the daily sync crashed and
rolled back (storing nothing). This layer pins that null flags become `False`.

Groq isn't involved; Canvas is mocked at the httpx transport boundary.
"""

import httpx

from app.canvas import fetch_assignments

BASE = "https://school.test"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_null_submission_flags_become_false():
    def handler(request):
        return httpx.Response(200, json=[{
            "id": 1, "name": "Final Project",
            "submission_types": ["online_upload"], "html_url": "", "description": "",
            "submission": {
                "workflow_state": "unsubmitted", "score": None,
                "late": None, "missing": None, "excused": None,
            },
        }])

    a = fetch_assignments(BASE, "tok", 1, client_for(handler))[0]

    # Explicit null flags must normalize to False (never None) — the columns are
    # NOT NULL and the sync upsert would otherwise violate that constraint.
    assert a["late"] is False
    assert a["missing"] is False
    assert a["excused"] is False


def test_true_and_false_flags_are_preserved():
    def handler(request):
        return httpx.Response(200, json=[{
            "id": 2, "name": "Lab", "submission_types": [], "html_url": "", "description": "",
            "submission": {"workflow_state": "submitted", "score": None,
                           "late": True, "missing": False, "excused": False},
        }])

    a = fetch_assignments(BASE, "tok", 1, client_for(handler))[0]

    assert a["late"] is True
    assert a["missing"] is False
    assert a["excused"] is False
