"""The nightly job runs on a Linux runner without torch or Windows-only packages.
Import every package it uses in a fresh interpreter that refuses those modules."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CODE = """
import sys
sys.path.insert(0, %r)
BLOCKED = ("sentence_transformers", "torch", "psutil", "win32api", "win32gui", "pywintypes", "winreg", "fastapi", "uvicorn")

class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked in the cloud job: {name}")
        return None

sys.meta_path.insert(0, Block())
import storage, review, sessions, patterns, schedule_gen, extractor, context, llm
import review.daily, sessions.job, extractor.job, extractor.worker
import schedule_gen.service, llm.approved, llm.factory
print("ok")
"""


def test_nightly_packages_import_without_laptop_only_modules():
    r = subprocess.run([sys.executable, "-I", "-c", CODE % str(ROOT)], cwd=ROOT, capture_output=True, text=True,
                       env={"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-1500:]
