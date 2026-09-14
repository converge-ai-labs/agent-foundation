"""Regenerate owned SDK bindings from sdk/openapi.json; --check never writes them."""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "sdk/codegen"
TARGETS = {
    "python": ROOT / "sdk/python/a13n/generated",
    "go": ROOT / "sdk/go/generated",
    "rust": ROOT / "sdk/rust/src/generated",
}


def run(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def prepare(document: dict, language: str) -> dict:
    """Generator-only annotations; the published 3.1 contract stays unchanged."""
    document = json.loads(json.dumps(document))
    schemas = document["components"]["schemas"]

    def unrestricted(schema: dict) -> bool:
        if "$ref" in schema:
            schema = schemas[schema["$ref"].split("/")[-1]]
        return not (schema.keys() - {"title", "description", "default", "x-rust-type"})

    def strip_defaults(schema: dict) -> None:
        # Only schema annotations: never delete responses.default or a JSON
        # property literally named "default".
        schema.pop("default", None)
        for keyword in ("properties", "patternProperties", "$defs"):
            for child in schema.get(keyword, {}).values():
                strip_defaults(child)
        for keyword in ("items", "additionalProperties", "not"):
            child = schema.get(keyword)
            if isinstance(child, dict):
                strip_defaults(child)
        for keyword in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for child in schema.get(keyword, []):
                strip_defaults(child)

    if language == "python":
        for schema in schemas.values():
            strip_defaults(schema)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if language == "python" and isinstance(value.get("schema"), dict):
                strip_defaults(value["schema"])
            if language == "rust":
                content = value.get("requestBody", {}).get("content", {})
                if len(content) == 1:
                    media_type, media = next(iter(content.items()))
                    if media.get("schema", {}).get("format") == "binary":
                        value["x-rust-binary-content-type"] = media_type
                branches = value.get("anyOf", [])
                if any(unrestricted(branch) for branch in branches):
                    value["x-rust-type"] = "serde_json::Value"
                if value.get("type") == "boolean" and isinstance(value.get("const"), bool):
                    value["x-rust-boolean-const"] = True
                    value["x-rust-boolean-true"] = value["const"]
                    value["x-rust-boolean-value"] = str(value["const"]).lower()
                if value.get("type") == "null":
                    value["x-rust-type"] = "()"
                if any(prop.get("writeOnly") for prop in value.get("properties", {}).values()):
                    value["x-rust-sensitive"] = True
                for name, prop in value.get("properties", {}).items():
                    if prop.get("type") == "null" and name not in value.get("required", []):
                        prop["x-rust-null-only"] = True
            for child in list(value.values()):
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    if language == "rust":
        # Response status is extensible, unlike closed union discriminator tags.
        schemas["RunStatus"]["x-rust-unknown-enum"] = True
    return document


def generate(language: str, document: dict, work: Path) -> Path:
    source = work / "openapi.json"
    source.write_text(json.dumps(prepare(document, language)))
    output = work / "output"
    if language == "python":
        run(
            "uv",
            "tool",
            "run",
            "--from",
            "openapi-python-client==0.29.1",
            "openapi-python-client",
            "generate",
            "--fail-on-warning",
            "--path",
            str(source),
            "--output-path",
            str(output),
            "--meta",
            "none",
            "--config",
            str(CONFIG / "python.yaml"),
            "--custom-template-path",
            str(CONFIG / "python"),
        )
        for path in output.rglob("*.py"):
            text = (
                path.read_text()
                .replace("import httpx\n", "import httpx2 as httpx\n")
                .replace("from httpx import ", "from httpx2 import ")
            )
            # Generated request models can contain write-only credentials. The
            # wire serializer remains unchanged; repr must not reveal their data.
            text = text.replace("@_attrs_define\n", "@_attrs_define(repr=False)\n").replace(
                "@define\n", "@define(repr=False)\n"
            )
            if '_kwargs["content"] = body.payload' in text:
                text = "from ...._binary import file_chunks\n" + text
                text = text.replace(
                    "    response = await client.get_async_httpx_client().request(",
                    '    kwargs["content"] = file_chunks(body.payload)\n\n    response = await client.get_async_httpx_client().request(',
                )
            text = text.replace("_get_kwargs", "build_request")
            if path.name == "client.py":
                text = text.replace("import ssl", "import ssl\nfrom types import TracebackType").replace(
                    "@define\n", "@define(repr=False)\n"
                )
                text = (
                    text.replace(
                        "self, *args: Any, **kwargs: Any",
                        "self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None",
                    )
                    .replace("__exit__(*args, **kwargs)", "__exit__(exc_type, exc_value, traceback)")
                    .replace("__aexit__(*args, **kwargs)", "__aexit__(exc_type, exc_value, traceback)")
                )
            path.write_text(text)
        run(
            "uv",
            "tool",
            "run",
            "--from",
            "ruff==0.16.3",
            "ruff",
            "check",
            "--fix",
            "--unsafe-fixes",
            "--config",
            str(ROOT / "sdk/python/pyproject.toml"),
            str(output),
        )
        run(
            "uv",
            "tool",
            "run",
            "--from",
            "ruff==0.16.3",
            "ruff",
            "format",
            "--config",
            str(ROOT / "sdk/python/pyproject.toml"),
            str(output),
        )
        # Tool-local caches are not part of the generated package.
        for cache in output.rglob(".ruff_cache"):
            shutil.rmtree(cache)
    elif language == "go":
        output.mkdir()
        run(
            "go",
            "run",
            "github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@v2.8.0",
            "--config",
            str(CONFIG / "go.yaml"),
            "-o",
            str(output / "client.gen.go"),
            str(source),
        )
        text = (output / "client.gen.go").read_text()
        secrets = "// Code generated by sdk/codegen/generate.py; DO NOT EDIT.\npackage generated\n\n"
        for name, schema in document["components"]["schemas"].items():
            if any(prop.get("writeOnly") for prop in schema.get("properties", {}).values()):
                assert f"type {name} struct" in text, f"Update the Go diagnostic adapter for {name}"
                secrets += f'func ({name}) String() string {{ return "{name} {{ .. }}" }}\n'
                secrets += f'func ({name}) GoString() string {{ return "{name} {{ .. }}" }}\n'
        (output / "secrets.gen.go").write_text(secrets)
        run("gofmt", "-w", str(output / "secrets.gen.go"))
    else:
        raw = work / "rust"
        run(
            "uv",
            "tool",
            "run",
            "--from",
            "openapi-generator-cli[jdk4py]==7.25.0",
            "openapi-generator-cli",
            "generate",
            "-i",
            str(source),
            "-g",
            "rust",
            "-t",
            str(CONFIG / "rust"),
            "-o",
            str(raw),
            "--additional-properties=hideGenerationTimestamp=true",
            "--global-property=apiDocs=false,modelDocs=false,apiTests=false,modelTests=false",
        )
        shutil.copytree(raw / "src", output)
        (output / "lib.rs").rename(output / "mod.rs")
        for path in output.rglob("*.rs"):
            path.write_text(
                path.read_text()
                .replace("crate::models", "crate::generated::models")
                .replace("crate::apis", "crate::generated::apis")
                .replace("use crate::{", "use crate::generated::{")
            )
        run("rustfmt", "--edition", "2024", str(output / "mod.rs"))
    return output


def files(path: Path) -> dict[str, bytes]:
    return {
        str(file.relative_to(path)): file.read_bytes()
        for file in path.rglob("*")
        if file.is_file() and "__pycache__" not in file.parts
    }


def install(output: Path, target: Path, *, check: bool) -> bool:
    actual, expected = files(target), files(output)
    changed = sorted(name for name in actual.keys() | expected.keys() if actual.get(name) != expected.get(name))
    if not changed:
        return True
    if check:
        print(f"Stale generated files in {target.relative_to(ROOT)}: " + ", ".join(changed[:20]))
        return False
    # Only generator-owned directories are replaced, including removed schemas.
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(output, target)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--language", choices=[*TARGETS, "typescript"], action="append")
    args = parser.parse_args()
    languages = args.language or [*TARGETS, "typescript"]
    run(
        "uv",
        "run",
        "--locked",
        "python",
        "scripts/export-a13n-service-openapi.py",
        *(["--check"] if args.check else []),
    )
    document = json.loads((ROOT / "sdk/openapi.json").read_text())
    valid = True
    for language in languages:
        if language == "typescript":
            run("node", "sdk/typescript/generate.mjs", *(["--check"] if args.check else []))
            continue
        with tempfile.TemporaryDirectory(prefix=f"a13n-{language}-") as temp:
            output = generate(language, document, Path(temp))
            valid = install(output, TARGETS[language], check=args.check) and valid
    if not valid:
        parser.exit(1, "SDK bindings changed. Run make sdk-generate and commit the result.\n")


if __name__ == "__main__":
    main()
