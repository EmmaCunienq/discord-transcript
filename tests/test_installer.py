"""Installeur Inno Setup (desktop/installer.iss) : cohérent avec le build PyInstaller et les données."""

import re

import app
from tests.conftest import ROOT

ISS = (ROOT / "desktop" / "installer.iss").read_text(encoding="utf-8")
SPEC = (ROOT / "desktop" / "DiscordTranscript.spec").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "desktop" / "main.py").read_text(encoding="utf-8")


def setting(name: str) -> str:
    return re.search(rf"^{name}=(.+)$", ISS, re.M).group(1).strip()


def test_version_au_format_x_y_z():
    assert re.fullmatch(r"\d+\.\d+\.\d+", app.__version__)


def test_installe_le_dossier_et_l_exe_construits_par_pyinstaller():
    name = re.search(r'name="(\w+)"', SPEC).group(1)
    assert f"..\\dist\\{name}\\*" in ISS
    assert ISS.count(f"{{app}}\\{name}.exe") >= 3  # raccourcis et lancement final


def test_installation_sans_admin_hors_du_dossier_des_donnees():
    """La désinstallation ou une mise à jour ne doit jamais toucher aux réunions et réglages."""
    assert setting("PrivilegesRequired") == "lowest"
    data_dir = re.search(r'LOCALAPPDATA.*?"(\w+)"', LAUNCHER).group(1)
    install_dir = setting("DefaultDirName")
    assert install_dir.startswith("{localappdata}\\Programs\\")
    assert install_dir.lower() != f"{{localappdata}}\\{data_dir}".lower()


def test_icone_presente():
    assert (ROOT / "desktop" / setting("SetupIconFile")).is_file()
