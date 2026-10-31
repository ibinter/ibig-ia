"""Déploiement : versions figées, scripts de sauvegarde et de restauration."""

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _name(requirement: str) -> str:
    return re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def test_lock_pins_every_dependency_with_hashes():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wanted = project["dependencies"] + [
        r for extra in ("postgres", "gmail") for r in project["optional-dependencies"][extra]]
    lock = (ROOT / "requirements.lock").read_text()
    pinned = {m.group(1).lower().replace("_", "-") for m in
              re.finditer(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==\S+ \\$", lock,
                          re.MULTILINE)}
    missing = sorted({_name(r) for r in wanted} - pinned)
    assert not missing, f"absents de requirements.lock (le régénérer) : {missing}"
    # Chaque paquet porte au moins une empreinte (installation --require-hashes).
    blocks = re.split(r"^(?=\S)", lock, flags=re.MULTILINE)
    assert all("--hash=sha256:" in b for b in blocks if "==" in b)


def test_dockerfile_installs_the_lock():
    docker = (ROOT / "Dockerfile").read_text()
    assert "--require-hashes -r requirements.lock" in docker
    assert "--no-deps ." in docker


def test_shell_scripts_parse():
    for script in ("deploy/sauvegarde.sh", "deploy/restauration.sh"):
        subprocess.run(["sh", "-n", str(ROOT / script)], check=True)
    subprocess.run(["bash", "-n", str(ROOT / "deploy/installer.sh")], check=True)
