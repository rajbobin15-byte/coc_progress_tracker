"""
Checks every 'from src.<module> import <name>' in app.py and src/*.py.
Reads files only; changes nothing. Run from the project folder.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def top_level_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for n in ast.walk(target):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def parse(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), None
    except SyntaxError as exc:
        return None, f"SYNTAX ERROR line {exc.lineno}: {exc.msg}"


files = [ROOT / "app.py"] + sorted(SRC.glob("*.py"))
problems = 0
defined = {}

print(f"Project folder: {ROOT}\n")
for path in files:
    rel = path.relative_to(ROOT)
    if not path.exists():
        print(f"MISSING   {rel}")
        problems += 1
        continue
    tree, error = parse(path)
    if error:
        print(f"BROKEN    {rel}: {error}")
        problems += 1
        continue
    defined[path.stem] = top_level_names(tree)
    if path.stat().st_size == 0 and path.name != "__init__.py":
        print(f"EMPTY     {rel}")
        problems += 1

print("Import check:")
for path in files:
    if not path.exists():
        continue
    tree, _ = parse(path)
    if tree is None:
        continue
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ImportFrom) and node.module
                and node.module.startswith("src.")):
            continue
        module = node.module.split(".", 1)[1]
        target = SRC / f"{module}.py"
        where = f"{path.relative_to(ROOT)} line {node.lineno}"
        if not target.exists():
            print(f"FAIL      {where}: src/{module}.py does not exist")
            problems += 1
            continue
        if module not in defined:
            print(f"FAIL      {where}: src/{module}.py has errors (see above)")
            problems += 1
            continue
        for alias in node.names:
            if alias.name != "*" and alias.name not in defined[module]:
                print(f"FAIL      {where}: src/{module}.py does not define '{alias.name}'")
                problems += 1

if problems:
    print(f"\n{problems} problem(s) found.")
    sys.exit(1)
print("\nAll imports resolve. Run: streamlit run app.py")
