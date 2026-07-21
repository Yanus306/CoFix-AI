import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


EXPECTED_GENERATED_FILES = {
    "__init__.py",
    "code_analysis_input_pb2.py",
    "code_analysis_input_pb2_grpc.py",
    "code_analysis_output_pb2.py",
    "issue_quiz_input_pb2.py",
    "issue_quiz_input_pb2_grpc.py",
    "issue_quiz_output_pb2.py",
    "learning_chat_input_pb2.py",
    "learning_chat_input_pb2_grpc.py",
    "learning_chat_output_pb2.py",
}


class ProtoGenerationTests(unittest.TestCase):
    def test_generator_recreates_only_the_expected_python_package(self):
        committed_directory = Path("generated_proto")
        with tempfile.TemporaryDirectory(prefix="cofix-generated-proto-") as temporary:
            generated_directory = Path(temporary, "generated_proto")
            subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_protos.py",
                    "--output-dir",
                    str(generated_directory),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            actual = {
                path.name for path in generated_directory.iterdir() if path.is_file()
            }
            self.assertEqual(actual, EXPECTED_GENERATED_FILES)

            for filename in EXPECTED_GENERATED_FILES:
                with self.subTest(filename=filename):
                    generated = (generated_directory / filename).read_bytes()
                    committed = (committed_directory / filename).read_bytes()
                    self.assertEqual(generated, committed)


if __name__ == "__main__":
    unittest.main()
