"""tools/recheck.decide(): escalation by kind, not by count."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from recheck import decide  # noqa: E402


def put(folder, name, data):
    (folder / name).write_text(json.dumps(data))


def checked(folder, content="h1"):
    put(folder, "checked.json", {"content": content})


def test_no_verdict_is_a_check(tmp_path):
    assert decide(tmp_path, "h1")[0] == "check"


def test_changed_content_clears_and_rechecks(tmp_path):
    checked(tmp_path, "old")
    put(tmp_path, "verdict.json", {"verdict": "PASS"})
    action, _reason, moved = decide(tmp_path, "new")
    assert (action, moved) == ("recheck", True)
    assert not (tmp_path / "verdict.json").exists()


def test_pass_is_done(tmp_path):
    checked(tmp_path)
    put(tmp_path, "verdict.json", {"verdict": "PASS"})
    assert decide(tmp_path, "h1")[0] == "done"


def test_convention_goes_to_a_person_at_once(tmp_path):
    checked(tmp_path)
    put(tmp_path, "verdict.json", {"verdict": "FAIL", "kind": "convention",
                                    "cause": "cover page counted"})
    assert decide(tmp_path, "h1")[0] == "human"


def test_checker_doubt_asks_a_second_checker(tmp_path):
    checked(tmp_path)
    put(tmp_path, "verdict.json", {"verdict": "UNSURE", "kind": "checker"})
    assert decide(tmp_path, "h1")[0] == "second-check"


def test_disagreeing_checkers_go_to_a_person(tmp_path):
    checked(tmp_path)
    put(tmp_path, "verdict.json", {"verdict": "UNSURE", "kind": "checker"})
    put(tmp_path, "verdict-second.json", {"verdict": "PASS"})
    assert decide(tmp_path, "h1")[0] == "human"


def test_agreeing_second_checker_settles_it(tmp_path):
    checked(tmp_path)
    put(tmp_path, "verdict.json", {"verdict": "UNSURE", "kind": "checker"})
    put(tmp_path, "verdict-second.json", {"verdict": "FAIL", "kind": "code"})
    assert decide(tmp_path, "h1")[0] == "fix"


def test_code_defect_gets_two_fixes_then_a_person(tmp_path):
    for round_, content in enumerate(("v1", "v2", "v3"), 1):
        checked(tmp_path, content)
        put(tmp_path, "verdict.json", {"verdict": "FAIL", "kind": "code",
                                        "cause": "wrong bounds"})
        action = decide(tmp_path, content)[0]
        assert action == ("fix" if round_ <= 2 else "human")
