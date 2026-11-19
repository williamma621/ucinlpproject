"""Build only the public sample, never the parent workspace or private notebook."""

from hashlib import sha256
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def package(destination=None):
    destination = Path(destination or ROOT.parent / "call-analytics-sample.zip")
    root_names = {"README.md", "pyproject.toml", ".gitignore", ".env.example"}
    folders = {"call_pipeline", "notebooks", "tests", "scripts", "examples", ".github"}
    extensions = {".py", ".json", ".jsonl", ".txt", ".ipynb", ".md", ".yml", ".csv"}
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(ROOT)
        if "__pycache__" in relative.parts or ".ipynb_checkpoints" in relative.parts:
            continue
        if not (str(relative) in root_names or
                relative.parts[0] in folders and path.suffix in extensions):
            continue
        content = path.read_bytes()
        if re.search(rb"AIza[0-9A-Za-z_-]{35}", content) or re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", content):
            raise ValueError(f"Potential credential in {relative}; archive was not created")
        files.append((relative, content))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, content in files:
            archive.writestr("call-analytics-sample/" + relative.as_posix(), content)
    print(f"Packaged {len(files)} public files: {destination.name}")
    print("SHA256:", sha256(destination.read_bytes()).hexdigest())
    return destination


if __name__ == "__main__":
    package()

