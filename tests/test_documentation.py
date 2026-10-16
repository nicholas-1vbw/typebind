"""Keep published examples executable and repository links valid for PyPI readers."""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def python_blocks(document: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", document, re.DOTALL)


def execute(source: str, tmp_path: Path) -> None:
    sample = tmp_path / "documentation.py"
    sample.write_text(source)
    result = subprocess.run(
        [sys.executable, str(sample)], capture_output=True, text=True, check=False, cwd=tmp_path
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_readme_examples(tmp_path: Path) -> None:
    blocks = python_blocks((ROOT / "README.md").read_text())
    assert blocks[0] == (ROOT / "examples/models.py").read_text()
    execute(blocks[0], tmp_path)
    (tmp_path / "commands.py").write_text((ROOT / "examples/commands.py").read_text())
    execute(blocks[1], tmp_path)
    execute(blocks[2], tmp_path)


def test_guide_examples(tmp_path: Path) -> None:
    source = "\n\n".join(
        block
        for name in ("models.md", "behaviors.md", "discovery.md")
        for block in python_blocks((ROOT / "docs" / name).read_text())
    )
    execute(source, tmp_path)


def test_document_links() -> None:
    repository = "https://github.com/nicholas-1vbw/typebind/blob/main/"
    for document in (ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))):
        prose = re.sub(r"```.*?```|`[^`]*`", "", document.read_text(), flags=re.DOTALL)
        targets: list[str] = re.findall(r"\[[^\]]+\]\(([^)]+)\)", prose)
        for target in targets:
            if target.startswith(repository):
                assert (ROOT / unquote(urlsplit(target[len(repository) :]).path)).exists(), target
            elif urlsplit(target).scheme:
                continue
            else:
                assert document.name != "README.md" or target.startswith("#"), target
                assert (document.parent / unquote(urlsplit(target).path)).exists(), target
