"""Static checks on the extension's JavaScript, because there is no Node here to lint it and a single bad import or icon
name stops a whole page from loading (this happened five times in a row: missing exports, an unknown icon, backticks
lost from template strings). It does not run the code; the real-browser check is the headless-Chrome scenario in
ASIDE_PLAN.md section 7."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parent.parent / "extension1"
JS = sorted(p for p in EXT.rglob("*.js") if "node_modules" not in p.parts)
HTML = sorted(EXT.rglob("*.html"))

EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([\w$]+)", re.M)
EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}", re.M)
IMPORT = re.compile(r"^import\s*\{([^}]*)\}\s*from\s*[\"']([^\"']+)[\"']", re.M)


def exports(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    names = set(EXPORT.findall(text))
    for block in EXPORT_LIST.findall(text):
        names |= {part.split(" as ")[-1].strip() for part in block.split(",") if part.strip()}
    return names


def test_there_is_javascript_to_check():
    assert len(JS) > 20


@pytest.mark.parametrize("path", JS, ids=lambda p: str(p.relative_to(EXT)))
def test_every_named_import_resolves_to_an_export(path):
    problems = []
    for block, spec in IMPORT.findall(path.read_text(encoding="utf-8")):
        if not spec.startswith("."):
            continue                                                  # only our own modules
        target = (path.parent / spec).resolve()
        if not target.exists():
            problems.append(f"{spec}: no such file")
            continue
        available = exports(target)
        for part in block.split(","):
            name = part.split(" as ")[0].strip()
            if name and name not in available:
                problems.append(f"{name} is not exported by {spec}")
    assert not problems, f"{path.relative_to(EXT)}: {problems}"


def test_every_icon_name_used_is_defined():
    defined = set(re.findall(r"^  ([\w]+):", (EXT / "shared" / "ui" / "icons.js").read_text(encoding="utf-8"), re.M))
    assert {"check", "close", "spark", "swap_horiz", "lightbulb"} <= defined
    used: dict[str, set[str]] = {}
    patterns = [r'data-icon="([a-z_0-9]+)"', r'\bicon\("([a-z_0-9]+)"', r'emptyState\("([a-z_0-9]+)"', r'emptyRow\("([a-z_0-9]+)"']
    for path in JS + HTML:
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            for name in re.findall(pattern, text):
                used.setdefault(name, set()).add(str(path.relative_to(EXT)))
    missing = {name: sorted(files) for name, files in used.items() if name not in defined}
    assert not missing, f"icons used but not defined in shared/ui/icons.js: {missing}"


@pytest.mark.parametrize("path", JS, ids=lambda p: str(p.relative_to(EXT)))
def test_no_template_string_lost_its_backticks(path):
    """`return get(/notes?limit=);` was a regex literal with no closing slash: the shape a stripped template string leaves."""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("//")[0] if "http" not in line and '"' not in line and "'" not in line and "`" not in line else line
        assert not re.search(r"\b(?:get|patch|del|post|request|fetch)\(\s*/[A-Za-z]", code), f"{path.name}:{number}: {line.strip()}"


def test_every_script_and_stylesheet_a_page_loads_exists():
    for page in HTML:
        text = page.read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="([^"#]+\.(?:js|css))"', text):
            if ref.startswith(("http", "//")):
                continue
            assert (page.parent / ref).resolve().exists(), f"{page.name} references missing {ref}"
