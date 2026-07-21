import unittest

import issue_quiz as quiz


def issue(dataset="variable_type", title="자료형 오류", code="total += value", **overrides):
    data = {
        "dataset": dataset,
        "title": title,
        "learning_directions": ["자료형 변환"],
        "code": code,
        "guide": "문제 원인과 해결 방법 및 핵심 원리",
    }
    data.update(overrides)
    return data


def problem(**overrides):
    data = {
        "question": "빈칸에 들어갈 코드는?",
        "code_block": {
            "language": "python",
            "content": "def parse(value):\n    return {{BLANK}}(value)",
        },
        "choices": [
            {"id": "A", "text": "str"},
            {"id": "B", "text": "int"},
            {"id": "C", "text": "list"},
            {"id": "D", "text": "dict"},
        ],
        "answer": "B",
        "explanation": "숫자 연산 전에는 문자열을 int로 변환해야 합니다.",
    }
    data.update(overrides)
    return data


class RequestValidationTests(unittest.TestCase):
    def test_accepts_three_problem_request_and_preserves_long_code(self):
        long_code = "function UserForm() {\n" + "  const value = 1;\n" * 20 + "}"
        request = {
            "selected_level": "medium",
            "problem_count": 3,
            "issues": [issue(code=long_code)],
        }

        result = quiz.validate_request(request)

        self.assertEqual(result["selected_level"], "medium")
        self.assertEqual(result["problem_count"], 3)
        self.assertEqual(result["issues"][0]["code"], long_code)
        self.assertEqual(
            list(result["issues"][0]),
            ["dataset", "title", "learning_directions", "code", "guide"],
        )

    def test_rejects_problem_count_other_than_three(self):
        with self.assertRaisesRegex(RuntimeError, "3"):
            quiz.validate_request({"selected_level": "easy", "problem_count": 2, "issues": [issue()]})

    def test_rejects_non_integer_problem_count_that_truncates_to_three(self):
        with self.assertRaisesRegex(RuntimeError, "3"):
            quiz.validate_request({"selected_level": "easy", "problem_count": 3.9, "issues": [issue()]})

    def test_rejects_null_required_issue_field(self):
        with self.assertRaisesRegex(RuntimeError, "title"):
            quiz.validate_request(
                {"selected_level": "easy", "problem_count": 3, "issues": [issue(title=None)]}
            )

    def test_rejects_unknown_level(self):
        with self.assertRaisesRegex(RuntimeError, "easy, medium, hard"):
            quiz.validate_request({"selected_level": "expert", "problem_count": 3, "issues": [issue()]})


class PriorityTests(unittest.TestCase):
    def test_frequency_is_primary_and_severity_is_secondary(self):
        issues = [
            issue("buffer_boundary", "경계 오류"),
            issue("variable_type", "변환 오류 1"),
            issue("variable_type", "변환 오류 2"),
            issue("edge_case", "빈 입력"),
        ]

        ranked = quiz.rank_issues(issues, "medium")

        self.assertEqual([item["dataset"] for item in ranked[:2]], ["variable_type", "variable_type"])
        self.assertEqual(ranked[2]["dataset"], "buffer_boundary")

    def test_repeated_dataset_lowers_internal_level_one_step(self):
        issues = [issue("variable_type", f"변환 오류 {index}") for index in range(3)]

        ranked = quiz.rank_issues(issues, "hard")

        self.assertTrue(all(item["effective_level"] == "medium" for item in ranked))

    def test_single_issue_creates_three_distinct_topic_angles(self):
        ranked = quiz.rank_issues([issue()], "medium")

        plan = quiz.build_topic_plan(ranked, 3)

        self.assertEqual(len(plan), 3)
        self.assertEqual(
            [item["angle"] for item in plan],
            ["concept", "application", "prevention"],
        )

    def test_severity_slot_prefers_a_different_dataset(self):
        issues = [
            issue("exception_handling", f"예외 오류 {index}")
            for index in range(3)
        ] + [issue("security_input", "입력 보안 오류")]

        plan = quiz.build_topic_plan(quiz.rank_issues(issues, "medium"), 3)

        self.assertEqual([item["dataset"] for item in plan[:2]], ["exception_handling"] * 2)
        self.assertEqual(plan[2]["dataset"], "security_input")

    def test_repeated_dataset_stays_in_second_slot_when_titles_are_identical(self):
        issues = [
            issue("variable_type", "같은 자료형 오류")
            for _index in range(3)
        ] + [
            issue("security_input", "입력 보안 오류"),
            issue("edge_case", "경계값 오류"),
        ]

        plan = quiz.build_topic_plan(quiz.rank_issues(issues, "medium"), 3)

        self.assertEqual([item["dataset"] for item in plan[:2]], ["variable_type"] * 2)
        self.assertEqual(plan[2]["dataset"], "security_input")


class OutputValidationTests(unittest.TestCase):
    def test_normalizes_three_problems_without_exposing_level(self):
        raw = {
            "problems": [
                problem(),
                problem(question="오류를 막을 조건은?", code_block=None),
                problem(question="올바른 변환 함수는?"),
            ]
        }

        result = quiz.normalize_quiz_set(raw, 3)

        self.assertEqual(list(result), ["problems"])
        self.assertEqual(len(result["problems"]), 3)
        self.assertEqual(
            list(result["problems"][0]),
            ["question", "code_block", "choices", "answer", "explanation"],
        )
        self.assertIsNone(result["problems"][1]["code_block"])
        self.assertNotIn("difficulty", result["problems"][0])

    def test_rejects_wrong_problem_count(self):
        with self.assertRaisesRegex(RuntimeError, "exactly 3"):
            quiz.normalize_quiz_set({"problems": [problem()]}, 3)

    def test_rejects_non_v1_problem_count_argument(self):
        with self.assertRaisesRegex(RuntimeError, "3 in V1"):
            quiz.normalize_quiz_set({"problems": [problem()]}, 1)

    def test_rejects_three_identical_problems(self):
        duplicate = problem()

        with self.assertRaisesRegex(RuntimeError, "distinct"):
            quiz.normalize_quiz_set({"problems": [duplicate, duplicate, duplicate]}, 3)

    def test_rejects_invalid_code_block(self):
        invalid = problem(code_block={"language": "css", "content": ""})

        with self.assertRaisesRegex(RuntimeError, "code_block"):
            quiz.normalize_quiz_set({"problems": [invalid, problem(), problem()]}, 3)

    def test_rejects_null_problem_text_instead_of_stringifying_it(self):
        invalid = problem(question=None)

        with self.assertRaisesRegex(RuntimeError, "question"):
            quiz.normalize_quiz_set({"problems": [invalid, problem(), problem()]}, 3)

    def test_rejects_invalid_choices(self):
        invalid = problem(choices=problem()["choices"][:3])

        with self.assertRaisesRegex(RuntimeError, "exactly 4"):
            quiz.normalize_quiz_set({"problems": [invalid, problem(), problem()]}, 3)


if __name__ == "__main__":
    unittest.main()
