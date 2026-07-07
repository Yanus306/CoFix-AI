import json
import os
import sys
import tempfile
import types
from pathlib import Path

import classify_error
import cofix
import recommend_problems


class Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeResponse:
    def __init__(self, text=None, output_text=None):
        self.text = text
        self.output_text = output_text


class FakeModels:
    def __init__(self, response_text):
        self.response_text = response_text
        self.last_model = None
        self.last_contents = None

    def generate_content(self, model, contents):
        self.last_model = model
        self.last_contents = contents
        return FakeResponse(text=self.response_text)


class FakeClient:
    def __init__(self, response_text):
        self.models = FakeModels(response_text)


class FakeGenAI:
    def __init__(self, response_text):
        self.response_text = response_text

    def Client(self):
        return FakeClient(self.response_text)


def with_fake_google(response_text, func):
    old_google = sys.modules.get("google")
    fake_google = types.ModuleType("google")
    fake_google.genai = FakeGenAI(response_text)
    sys.modules["google"] = fake_google
    try:
        return func()
    finally:
        if old_google is None:
            sys.modules.pop("google", None)
        else:
            sys.modules["google"] = old_google


def with_env(key, value, func):
    old = os.environ.get(key)
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value
    try:
        return func()
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(f"{message}: expected={expected!r}, actual={actual!r}")


def assert_in(member, container, message):
    if member not in container:
        raise AssertionError(f"{message}: {member!r} not in {container!r}")


def expect_raises(exc_type, func, message):
    try:
        func()
    except exc_type:
        return
    raise AssertionError(message)


def categories():
    return [
        {"key": "edge_case", "name": "경계값 처리 실패", "condition": "edge"},
        {"key": "clean_code", "name": "클린코드 위배", "condition": "clean"},
        {"key": "data_validation", "name": "입력값 검증 오류", "condition": "validation"},
        {"key": "algo_selection", "name": "알고리즘 선택 부적절", "condition": "algo"},
        {"key": "exception_handling", "name": "예외 처리 오류", "condition": "exception"},
    ]


def problems():
    return [
        {
            "title": "A",
            "level": "easy",
            "tags": ["edge_case", "clean_code"],
            "statement": "solve A",
            "input": "input A",
            "output": "output A",
            "constraints": ["c1"],
            "examples": [{"input": "1", "output": "1"}],
        },
        {
            "title": "B",
            "level": "medium",
            "tags": ["data_validation"],
            "statement": "solve B",
            "input": "input B",
            "output": "output B",
            "constraints": [],
            "examples": [],
        },
    ]


def cofix_experiments():
    tests = []

    tests.append(("cofix 01 strip plain json", lambda: assert_equal(cofix.strip_json_fence('{"a":1}'), '{"a":1}', "plain JSON unchanged")))
    tests.append(("cofix 02 strip fenced json", lambda: assert_equal(cofix.strip_json_fence('```json\n{"a":1}\n```'), '{"a":1}', "fenced JSON stripped")))
    tests.append(("cofix 03 strip non-fence text", lambda: assert_equal(cofix.strip_json_fence("hello"), "hello", "non-fence unchanged")))
    tests.append(("cofix 04 extract output_text", lambda: assert_equal(cofix.extract_text(FakeResponse(text="bad", output_text="good")), "good", "output_text preferred")))
    tests.append(("cofix 05 extract text", lambda: assert_equal(cofix.extract_text(FakeResponse(text="good")), "good", "text fallback")))
    tests.append(("cofix 06 prompt contains schema", lambda: assert_in('"fixed_code"', cofix.build_prompt("x=1", "stdin", None, "python"), "prompt schema")))
    tests.append(("cofix 07 prompt contains source", lambda: assert_in("Source: file.py", cofix.build_prompt("x=1", "file.py", "fix", None), "prompt source")))

    def t08():
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
            def run():
                cofix.load_env_file(env)
                assert_equal(os.environ.get("GEMINI_API_KEY"), "abc", "env file loaded")
            with_env("GEMINI_API_KEY", None, run)
    tests.append(("cofix 08 load env file", t08))

    def t09():
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("GEMINI_API_KEY=file\n", encoding="utf-8")
            def run():
                cofix.load_env_file(env)
                assert_equal(os.environ.get("GEMINI_API_KEY"), "existing", "env not overridden")
            with_env("GEMINI_API_KEY", "existing", run)
    tests.append(("cofix 09 load env does not override", t09))

    tests.append(("cofix 10 render list parts", lambda: assert_in("1. changed", cofix.render_markdown({"fixed_code": "x=2", "modified_parts": ["changed"]}), "list parts rendered")))
    tests.append(("cofix 11 render no changes", lambda: assert_in("- No changes", cofix.render_markdown({"fixed_code": "x=1"}), "no changes rendered")))
    tests.append(("cofix 12 render dict part", lambda: assert_in("rename", cofix.render_markdown({"fixed_code": "x=1", "modified_parts": [{"part": "rename"}]}), "dict part rendered")))
    tests.append(("cofix 13 render dict change", lambda: assert_in("fix", cofix.render_markdown({"fixed_code": "x=1", "modified_parts": [{"change": "fix"}]}), "dict change rendered")))
    tests.append(("cofix 14 render string part once", lambda: assert_in("1. one change", cofix.render_markdown({"fixed_code": "x=1", "modified_parts": "one change"}), "string part rendered once")))
    tests.append(("cofix 15 render missing code", lambda: assert_in("## Fixed Code", cofix.render_markdown({"modified_parts": []}), "missing code safe")))

    def t16():
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
            with_env("GEMINI_API_KEY", None, lambda: assert_equal(cofix.get_gemini_api_key(env), "abc", "get api key"))
            os.environ.pop("GEMINI_API_KEY", None)
    tests.append(("cofix 16 get api key from env file", t16))

    def t17():
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "code.py"
            file.write_text("print('안녕')", encoding="utf-8")
            text, source = cofix.read_code(Args(file=str(file), encoding="utf-8"))
            assert_equal(text, "print('안녕')", "read code text")
            assert_equal(source, str(file), "read code source")
    tests.append(("cofix 17 read code file", t17))

    def t18():
        response = '{"fixed_code":"x=2","modified_parts":["changed x"]}'
        args = Args(env_file="missing.env", model="m", instruction=None, language=None)
        def run():
            return with_fake_google(response, lambda: cofix.request_fix("x=1", "stdin", args))
        result = with_env("GEMINI_API_KEY", "fake", run)
        assert_equal(result["fixed_code"], "x=2", "mock request fixed code")
    tests.append(("cofix 18 request_fix mocked json", t18))

    def t19():
        response = '```json\n{"fixed_code":"x=3","modified_parts":["fenced"]}\n```'
        args = Args(env_file="missing.env", model="m", instruction=None, language=None)
        result = with_env("GEMINI_API_KEY", "fake", lambda: with_fake_google(response, lambda: cofix.request_fix("x=1", "stdin", args)))
        assert_equal(result["modified_parts"], ["fenced"], "mock request fenced")
    tests.append(("cofix 19 request_fix fenced json", t19))

    tests.append(("cofix 20 missing api key raises", lambda: with_env("GEMINI_API_KEY", None, lambda: expect_raises(RuntimeError, lambda: cofix.request_fix("x=1", "stdin", Args(env_file="missing.env", model="m", instruction=None, language=None)), "missing api key"))))
    return tests


def classify_experiments():
    cats = categories()
    tests = []
    tests.append(("classify 01 normalize valid", lambda: assert_equal(classify_error.normalize_labels(["edge_case"], cats), ["edge_case"], "valid label")))
    tests.append(("classify 02 normalize invalid removed", lambda: assert_equal(classify_error.normalize_labels(["edge_case", "bad"], cats), ["edge_case"], "invalid removed")))
    tests.append(("classify 03 normalize de-dupe", lambda: assert_equal(classify_error.normalize_labels(["edge_case", "edge_case"], cats), ["edge_case"], "dedupe")))

    def t04():
        profile = {"edge_case": 0, "total_submit_count": 0, "total_error_count": 0}
        classify_error.update_profile(profile, ["edge_case"])
        assert_equal(profile["edge_case"], 1, "label incremented")
        assert_equal(profile["total_submit_count"], 1, "submit count")
    tests.append(("classify 04 update profile increments", t04))

    def t05():
        profile = {"total_submit_count": 0, "total_error_count": 0}
        classify_error.update_profile(profile, [])
        assert_equal(profile["total_submit_count"], 1, "empty labels still submit")
        assert_equal(profile["total_error_count"], 0, "no errors")
    tests.append(("classify 05 update profile no labels", t05))

    def t06():
        profile = {"edge_case": 2, "algo_selection": 3, "clean_code": 4}
        classify_error.recompute_rollups(profile)
        assert_equal(profile["syntax_fail_count"], 2, "syntax rollup")
        assert_equal(profile["algorithm_fail_count"], 3, "algorithm rollup")
        assert_equal(profile["cleancode_fail_count"], 4, "clean rollup")
    tests.append(("classify 06 recompute rollups", t06))

    tests.append(("classify 07 render no labels", lambda: assert_in("- No labels", classify_error.render([], cats, {}, False), "no label render")))
    tests.append(("classify 08 render label names", lambda: assert_in("경계값", classify_error.render(["edge_case"], cats, {}, False), "label name render")))
    tests.append(("classify 09 prompt contains categories", lambda: assert_in("edge_case: edge", classify_error.build_prompt("x", cats, None), "prompt category")))
    tests.append(("classify 10 strip fenced", lambda: assert_equal(classify_error.strip_json_fence("```json\n{\"labels\":[]}\n```"), "{\"labels\":[]}", "strip fence")))

    def t11():
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "data.json"
            file.write_text('{"a":1}', encoding="utf-8")
            assert_equal(classify_error.read_json(file), {"a": 1}, "read json")
    tests.append(("classify 11 read json", t11))

    def t12():
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "data.json"
            classify_error.write_json(file, {"한글": 1})
            assert_in("한글", file.read_text(encoding="utf-8"), "write utf8 json")
    tests.append(("classify 12 write json utf8", t12))

    def t13():
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "old.py"
            file.write_text("x=None", encoding="utf-8")
            assert_equal(classify_error.read_text_input(str(file), "utf-8"), "x=None", "read old code")
    tests.append(("classify 13 read text input", t13))

    def t14():
        response = '{"labels":["edge_case","clean_code"]}'
        args = Args(env_file="missing.env", model="m", instruction=None)
        labels = with_env("GEMINI_API_KEY", "fake", lambda: with_fake_google(response, lambda: classify_error.request_labels("x", cats, args)))
        assert_equal(labels, ["edge_case", "clean_code"], "mock labels object")
    tests.append(("classify 14 request labels object", t14))

    def t15():
        response = '["edge_case"]'
        args = Args(env_file="missing.env", model="m", instruction=None)
        labels = with_env("GEMINI_API_KEY", "fake", lambda: with_fake_google(response, lambda: classify_error.request_labels("x", cats, args)))
        assert_equal(labels, ["edge_case"], "mock labels array")
    tests.append(("classify 15 request labels array", t15))

    def t16():
        response = '{"labels":"edge_case"}'
        args = Args(env_file="missing.env", model="m", instruction=None)
        labels = with_env("GEMINI_API_KEY", "fake", lambda: with_fake_google(response, lambda: classify_error.request_labels("x", cats, args)))
        assert_equal(labels, ["edge_case"], "mock labels string")
    tests.append(("classify 16 request labels string", t16))

    tests.append(("classify 17 normalize trims", lambda: assert_equal(classify_error.normalize_labels([" edge_case "], cats), ["edge_case"], "trim label")))

    def t18():
        profile = {"edge_case": "2", "total_submit_count": "1", "total_error_count": "2"}
        classify_error.update_profile(profile, ["edge_case"])
        assert_equal(profile["edge_case"], 3, "string numeric increment")
    tests.append(("classify 18 profile string numbers", t18))

    def t19():
        profile = {}
        classify_error.update_profile(profile, ["edge_case"])
        assert_equal(profile["edge_case"], 1, "missing key created")
    tests.append(("classify 19 profile missing key", t19))

    tests.append(("classify 20 missing api key raises", lambda: with_env("GEMINI_API_KEY", None, lambda: expect_raises(RuntimeError, lambda: classify_error.request_labels("x", cats, Args(env_file="missing.env", model="m", instruction=None)), "missing api key"))))
    return tests


def recommend_experiments():
    cats = categories()
    probs = problems()
    tests = []
    tests.append(("recommend 01 top issues order", lambda: assert_equal(recommend_problems.top_issues({"edge_case": 2, "clean_code": 1}, cats, 3), [("edge_case", 2), ("clean_code", 1)], "top order")))
    tests.append(("recommend 02 top issues excludes zero", lambda: assert_equal(recommend_problems.top_issues({"edge_case": 0}, cats, 3), [], "zero excluded")))
    tests.append(("recommend 03 top zero empty", lambda: assert_equal(recommend_problems.top_issues({"edge_case": 1}, cats, 0), [], "top zero")))
    tests.append(("recommend 04 top negative empty", lambda: assert_equal(recommend_problems.top_issues({"edge_case": 1}, cats, -1), [], "top negative")))
    tests.append(("recommend 05 local match", lambda: assert_equal(recommend_problems.pick_local_problems([("edge_case", 1)], probs, 1)[0][1]["title"], "A", "local match")))
    tests.append(("recommend 06 local duplicate avoided", lambda: assert_equal(len(recommend_problems.pick_local_problems([("edge_case", 1), ("clean_code", 1)], probs, 1)), 1, "duplicate title avoided")))
    tests.append(("recommend 07 fallback generated", lambda: assert_equal(recommend_problems.pick_local_problems([("unknown", 1)], probs, 1)[0][1]["tags"], ["unknown"], "fallback tags")))
    tests.append(("recommend 08 per issue zero none", lambda: assert_equal(recommend_problems.pick_local_problems([("edge_case", 1)], probs, 0), [], "per issue zero")))
    tests.append(("recommend 09 fallback fields", lambda: assert_in("statement", recommend_problems.make_fallback_problem("x"), "fallback statement")))
    tests.append(("recommend 10 render no issues", lambda: assert_in("No issue counts yet", recommend_problems.render([], [], cats, "local"), "render no issues")))
    tests.append(("recommend 11 render full problem", lambda: assert_in("문제", recommend_problems.render([("edge_case", 1)], [("edge_case", probs[0])], cats, "local"), "render statement")))
    tests.append(("recommend 12 render examples skip bad", lambda: assert_equal(recommend_problems.render_examples(["bad"]), [], "skip non-dict example")))
    tests.append(("recommend 13 indent multiline", lambda: assert_equal(recommend_problems.indent_block("a\nb"), "   a\n   b", "indent")))
    tests.append(("recommend 14 normalize examples non-list", lambda: assert_equal(recommend_problems.normalize_problem({"examples": "bad"}, "edge_case")["examples"], [], "normalize examples")))
    tests.append(("recommend 15 normalize constraints string", lambda: assert_equal(recommend_problems.normalize_problem({"constraints": "c"}, "edge_case")["constraints"], ["c"], "normalize constraints")))

    def t16():
        response = json.dumps({"problems": [{"issue_key": "edge_case", "title": "AI", "statement": "S", "input": "I", "output": "O", "constraints": [], "examples": []}]})
        args = Args(env_file="missing.env", model="m", per_issue=1)
        result = with_env("GEMINI_API_KEY", "fake", lambda: with_fake_google(response, lambda: recommend_problems.request_ai_problems([("edge_case", 1)], cats, args)))
        assert_equal(result[0][1]["title"], "AI", "ai valid")
    tests.append(("recommend 16 ai problems valid", t16))

    def t17():
        response = json.dumps({"problems": [{"issue_key": "bad", "title": "AI"}]})
        args = Args(env_file="missing.env", model="m", per_issue=1)
        result = with_env("GEMINI_API_KEY", "fake", lambda: with_fake_google(response, lambda: recommend_problems.request_ai_problems([("edge_case", 1)], cats, args)))
        assert_equal(result, [], "invalid issue filtered")
    tests.append(("recommend 17 ai invalid issue filtered", t17))

    def t18():
        response = '```json\n{"problems":[{"issue_key":"edge_case","title":"AI","statement":"S","input":"I","output":"O","constraints":[],"examples":[]}]}\n```'
        args = Args(env_file="missing.env", model="m", per_issue=1)
        result = with_env("GEMINI_API_KEY", "fake", lambda: with_fake_google(response, lambda: recommend_problems.request_ai_problems([("edge_case", 1)], cats, args)))
        assert_equal(result[0][1]["title"], "AI", "fenced ai")
    tests.append(("recommend 18 ai fenced", t18))

    tests.append(("recommend 19 ai missing key raises", lambda: with_env("GEMINI_API_KEY", None, lambda: expect_raises(RuntimeError, lambda: recommend_problems.request_ai_problems([("edge_case", 1)], cats, Args(env_file="missing.env", model="m", per_issue=1)), "missing key"))))
    tests.append(("recommend 20 prompt contains no solution rule", lambda: assert_in("Do not include solutions", recommend_problems.build_ai_prompt([("edge_case", 1)], cats, 1), "prompt no solution")))
    return tests


def run_tests():
    suites = [
        ("cofix.py", cofix_experiments()),
        ("classify_error.py", classify_experiments()),
        ("recommend_problems.py", recommend_experiments()),
    ]
    total = 0
    failed = []
    for suite_name, tests in suites:
        print(f"\n# {suite_name}")
        for name, test in tests:
            total += 1
            try:
                test()
                print(f"PASS {name}")
            except Exception as exc:
                failed.append((name, exc))
                print(f"FAIL {name}: {exc}")

    print(f"\nTOTAL: {total}")
    print(f"PASSED: {total - len(failed)}")
    print(f"FAILED: {len(failed)}")
    if failed:
        print("\nFailures:")
        for name, exc in failed:
            print(f"- {name}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_tests())
