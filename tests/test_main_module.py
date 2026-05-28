import subprocess
import sys
from pathlib import Path


def test_main_module_script_execution_help():
    root = Path(__file__).resolve().parents[1]
    entrypoint = root / "confluence_markdown_mcp" / "__main__.py"

    result = subprocess.run(
        [sys.executable, str(entrypoint), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
