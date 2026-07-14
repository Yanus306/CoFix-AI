import unittest

from issue_quiz import normalize_quiz


def valid_quiz(**overrides):
    data = {
        "question": "이 오류를 막기 위해 확인할 값은?",
        "choices": [
            {"id": "A", "text": "None 검사"},
            {"id": "B", "text": "길이 검사"},
            {"id": "C", "text": "타입 변환"},
            {"id": "D", "text": "정렬 수행"},
        ],
        "answer": "A",
        "explanation": "값이 없는 경우를 먼저 처리해야 합니다.",
    }
    data.update(overrides)
    return data


class NormalizeQuizTests(unittest.TestCase):
    def test_preserves_existing_output_shape(self):
        result = normalize_quiz(valid_quiz())

        self.assertEqual(
            list(result),
            ["question", "choices", "answer", "explanation"],
        )

    def test_rejects_answer_text_that_only_starts_with_choice_id(self):
        with self.assertRaisesRegex(RuntimeError, "A, B, C, D"):
            normalize_quiz(valid_quiz(answer="Answer A"))

    def test_rejects_empty_explanation(self):
        with self.assertRaisesRegex(RuntimeError, "explanation"):
            normalize_quiz(valid_quiz(explanation=""))

    def test_rejects_more_than_four_choices(self):
        choices = valid_quiz()["choices"] + [{"id": "E", "text": "추가 선택지"}]

        with self.assertRaisesRegex(RuntimeError, "exactly 4"):
            normalize_quiz(valid_quiz(choices=choices))

    def test_rejects_missing_or_invalid_choice_ids(self):
        choices = valid_quiz()["choices"]
        choices[0] = {"text": "None 검사"}

        with self.assertRaisesRegex(RuntimeError, "id"):
            normalize_quiz(valid_quiz(choices=choices))


if __name__ == "__main__":
    unittest.main()
