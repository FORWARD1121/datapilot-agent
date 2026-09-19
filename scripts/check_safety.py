"""Focused AST safety gate, not a substitute for a general linter or penetration test."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect_source(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in {"eval", "exec", "system", "popen"}:
            findings.append(f"{filename}:{node.lineno}: forbidden execution primitive {name}")
        if any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
            findings.append(f"{filename}:{node.lineno}: shell=True is forbidden")
        if name == "text" and node.args and not isinstance(node.args[0], ast.Constant):
            findings.append(f"{filename}:{node.lineno}: SQL text must be a literal")
    return findings


def main() -> int:
    files = sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py"))
    findings = [finding for path in files for finding in inspect_source(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))]
    for finding in findings:
        print(finding)
    print(f"AST safety gate: {len(files)} files, {len(findings)} findings")
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
