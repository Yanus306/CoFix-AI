"""Generate Python protobuf and gRPC bindings outside the schema submodule."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from grpc_tools import protoc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PROJECT_ROOT / "proto"
OUTPUT_DIR = PROJECT_ROOT / "generated_proto"
PACKAGE_INIT = '"""Generated Python bindings for the schemas in the proto submodule."""\n'

PROTO_FILES = (
    "code_analysis_input.proto",
    "code_analysis_output.proto",
    "issue_quiz_input.proto",
    "issue_quiz_output.proto",
    "learning_chat_input.proto",
    "learning_chat_output.proto",
)
SERVICE_PROTO_FILES = (
    "code_analysis_input.proto",
    "issue_quiz_input.proto",
    "learning_chat_input.proto",
)


def _run_protoc(arguments):
    result = protoc.main(["grpc_tools.protoc", *arguments])
    if result != 0:
        raise RuntimeError(f"protoc failed with exit code {result}.")


def _generated_module_text(path):
    text = path.read_text(encoding="utf-8")
    text = text.replace("from proto import ", "from generated_proto import ")
    return text.replace("'proto.", "'generated_proto.")


def generate(output_dir=OUTPUT_DIR):
    output_dir = Path(output_dir).resolve()
    missing = [name for name in PROTO_FILES if not (SCHEMA_DIR / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing protobuf schemas: {missing}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "__init__.py").write_text(
        PACKAGE_INIT,
        encoding="utf-8",
        newline="\n",
    )
    with TemporaryDirectory(prefix="cofix-proto-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        common = [f"-I{PROJECT_ROOT}"]
        schema_paths = [str(SCHEMA_DIR / name) for name in PROTO_FILES]
        service_paths = [str(SCHEMA_DIR / name) for name in SERVICE_PROTO_FILES]
        _run_protoc([*common, f"--python_out={temporary_root}", *schema_paths])
        _run_protoc([*common, f"--grpc_python_out={temporary_root}", *service_paths])

        generated_source = temporary_root / "proto"
        expected = set()
        for source in generated_source.glob("*_pb2*.py"):
            destination = output_dir / source.name
            destination.write_text(
                _generated_module_text(source),
                encoding="utf-8",
                newline="\n",
            )
            expected.add(destination.name)

    for stale in output_dir.glob("*_pb2*.py"):
        if stale.name not in expected:
            stale.unlink()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory that will contain the generated Python package.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    generate(parse_args().output_dir)
