"""Widget SARA et relais PHP des sites : fichiers livrés dans integrations/sara/."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SARA = Path(__file__).resolve().parents[1] / "integrations" / "sara"
php = pytest.mark.skipif(shutil.which("php") is None, reason="PHP absent")


@php
@pytest.mark.parametrize("name", ["sara-relay.php", "sara-config.example.php"])
def test_php_files_have_no_syntax_error(name):
    out = subprocess.run(["php", "-l", str(SARA / name)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout + out.stderr


@php
def test_relay_refuses_to_run_without_a_configured_key():
    # Sans sara-config.php (jamais versionné) : 503 clair, aucun appel à l'agent
    assert not (SARA / "sara-config.php").exists()
    out = subprocess.run(["php", str(SARA / "sara-relay.php")], capture_output=True,
                         text=True, encoding="utf-8", check=False)
    assert "non configurée" in json.loads(out.stdout)["erreur"]


def test_widget_never_injects_html_nor_holds_a_key():
    js = (SARA / "sara-widget.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    assert "Bearer" not in js and "api_key" not in js and "IBIG_SARA_API_KEY" not in js
    assert "textContent" in js  # réponses affichées en texte brut


def test_config_example_is_empty_and_documented():
    cfg = (SARA / "sara-config.example.php").read_text(encoding="utf-8")
    assert "'api_key' => ''," in cfg and "allowed_origins" in cfg
    assert "IBIG_SARA_API_KEY" in cfg
    assert "sara-config.php" in (SARA / "LISEZMOI.md").read_text(encoding="utf-8")
