"""Qualité du code : pas de code mort détectable, scripts syntaxiquement valides."""

import re
import shutil
import subprocess
import sys

import pytest

from tests.conftest import ROOT

PAGES = [ROOT / "static" / "index.html", ROOT / "static" / "settings.html"]


def test_pyflakes_sans_import_ni_variable_inutilises():
    res = subprocess.run([sys.executable, "-m", "pyflakes", "app", "desktop", "tests"],
                         cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_toutes_les_icones_declarees_sont_utilisees(page):
    html = page.read_text(encoding="utf-8")
    unused = [s for s in re.findall(r'<symbol id="([^"]+)"', html) if f'href="#{s}"' not in html]
    assert unused == []


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_elements_references_par_le_script_existent(page):
    html = page.read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    script = html[html.index("<script>"):]
    missing = sorted({i for i in re.findall(r'\$\("([^"]+)"\)', script) if i not in ids})
    assert missing == []


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js introuvable")
@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_script_de_la_page_valide(page, tmp_path):
    html = page.read_text(encoding="utf-8")
    script = tmp_path / "page.js"
    script.write_text(html[html.index("<script>") + 8:html.index("</script>")], encoding="utf-8")
    res = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js introuvable")
def test_script_du_bot_valide():
    res = subprocess.run(["node", "--check", str(ROOT / "bot" / "index.js")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js introuvable")
@pytest.mark.parametrize("filename", ["themes.js", "app-menu.js", "app-icon.js"])
def test_scripts_partages_valides(filename):
    res = subprocess.run(["node", "--check", str(ROOT / "static" / filename)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
