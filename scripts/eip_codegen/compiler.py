from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import grpc_tools
from google.protobuf import descriptor_pb2
from grpc_tools import protoc

PROTO_ROOT = Path("proto")
EIP_PROTO_ROOT = PROTO_ROOT / "agent-envd" / "eip" / "v1"


def compile_descriptor(output_dir: Path) -> tuple[descriptor_pb2.FileDescriptorSet, bytes, ModuleType]:
    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor_path = output_dir / "descriptor.pb"
    python_out = output_dir / "bootstrap"
    python_out.mkdir(parents=True, exist_ok=True)
    bundled_proto = Path(grpc_tools.__file__).parent / "_proto"
    proto_files = sorted(path.relative_to(PROTO_ROOT).as_posix() for path in EIP_PROTO_ROOT.glob("*.proto"))

    descriptor_args = [
        "grpc_tools.protoc",
        f"-I{PROTO_ROOT}",
        f"-I{bundled_proto}",
        "--include_imports",
        f"--descriptor_set_out={descriptor_path}",
        *proto_files,
    ]
    if protoc.main(descriptor_args) != 0:
        raise RuntimeError("failed to compile EIP descriptor")

    options_args = [
        "grpc_tools.protoc",
        f"-I{PROTO_ROOT}",
        f"-I{bundled_proto}",
        f"--python_out={python_out}",
        "agent-envd/eip/v1/options.proto",
    ]
    if protoc.main(options_args) != 0:
        raise RuntimeError("failed to compile EIP custom options")

    option_candidates = list(python_out.rglob("options_pb2.py"))
    if len(option_candidates) != 1:
        raise RuntimeError(f"expected one generated options module, found {option_candidates}")
    options_path = option_candidates[0]
    spec = importlib.util.spec_from_file_location("a13n_eip_options_pb2", options_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load generated EIP custom options")
    options_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = options_module
    spec.loader.exec_module(options_module)

    descriptor_bytes = descriptor_path.read_bytes()
    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.ParseFromString(descriptor_bytes)
    return descriptor_set, descriptor_bytes, options_module
