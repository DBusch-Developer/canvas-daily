from datetime import datetime

import httpx

from app.canvas import fetch_assignments

BASE = "https://school.test"


def client_for(handler):
    """An httpx client whose requests are answered in-process — no network."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_core_assignment_fields():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[{
            "id": 101,
            "name": "Essay 1",
            "due_at": "2026-06-20T23:59:00Z",
            "points_possible": 100,
            "submission_types": ["online_text_entry"],
            "html_url": f"{BASE}/courses/1/assignments/101",
            "description": "<p>Write five pages.</p>",
        }])

    result = fetch_assignments(BASE, "secret-token", 1, client_for(handler))

    assert len(result) == 1
    a = result[0]
    assert a["canvas_assignment_id"] == 101
    assert a["name"] == "Essay 1"
    assert a["points_possible"] == 100
    assert isinstance(a["due_at"], datetime) and a["due_at"].year == 2026
    # The token rides in the Authorization header, never the URL.
    assert captured[0].headers["Authorization"] == "Bearer secret-token"


def test_follows_link_header_pagination():
    def handler(request):
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[
                {"id": 2, "name": "B", "due_at": None, "points_possible": None,
                 "submission_types": [], "html_url": "", "description": ""},
            ])
        next_url = f"{BASE}/api/v1/courses/1/assignments?page=2"
        return httpx.Response(
            200,
            json=[{"id": 1, "name": "A", "due_at": None, "points_possible": None,
                   "submission_types": [], "html_url": "", "description": ""}],
            headers={"Link": f'<{next_url}>; rel="next"'},
        )

    result = fetch_assignments(BASE, "tok", 1, client_for(handler))

    # Both pages assembled, not just page one.
    assert [a["canvas_assignment_id"] for a in result] == [1, 2]


def test_parses_included_submission_state():
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(200, json=[{
            "id": 5, "name": "Quiz", "due_at": None, "points_possible": 10,
            "submission_types": ["online_quiz"], "html_url": "", "description": "",
            "submission": {
                "workflow_state": "graded",
                "score": 8.5,
                "submitted_at": "2026-06-10T12:00:00Z",
                "late": True,
                "missing": False,
                "excused": False,
            },
        }])

    result = fetch_assignments(BASE, "tok", 1, client_for(handler))
    a = result[0]

    # The fetch asks Canvas to fold in the user's own submission state.
    assert captured[0].url.params.get_list("include[]") == ["submission"]
    assert a["workflow_state"] == "graded"
    assert a["score"] == 8.5
    assert isinstance(a["submitted_at"], datetime)
    assert a["late"] is True
    assert a["missing"] is False
    assert a["excused"] is False


def test_sanitizes_description_html():
    def handler(request):
        return httpx.Response(200, json=[{
            "id": 9, "name": "X", "due_at": None, "points_possible": None,
            "submission_types": [], "html_url": "",
            "description": '<p>Read chapter 3.</p><script>steal(document.cookie)</script>',
        }])

    result = fetch_assignments(BASE, "tok", 1, client_for(handler))
    desc = result[0]["description"]

    assert "Read chapter 3." in desc
    assert "<script>" not in desc
    assert "steal" not in desc  # script contents stripped, not just the tag


def test_null_score_preserved_not_zeroed():
    def handler(request):
        return httpx.Response(200, json=[{
            "id": 7, "name": "Ungraded essay", "due_at": None, "points_possible": 50,
            "submission_types": ["online_text_entry"], "html_url": "", "description": "",
            "submission": {
                "workflow_state": "submitted",
                "score": None,
                "submitted_at": "2026-06-11T09:00:00Z",
                "late": False,
                "missing": False,
                "excused": False,
            },
        }])

    result = fetch_assignments(BASE, "tok", 1, client_for(handler))

    # Null until graded — preserved as None, never coerced to 0.
    assert result[0]["score"] is None
