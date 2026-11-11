"""Couleurs du cadre WinForms hors écran et attributs du titre Windows."""

import ctypes
import base64
import struct
import os
from types import SimpleNamespace

import pytest

from desktop import window_theme
from tests.conftest import ROOT


GREEN = {"background": "#263b31", "text": "#f2e7c8", "border": "#4e624a", "dark": True}
LIGHT = {"background": "#fffaf0", "text": "#254958", "border": "#d4dfd5", "dark": False}


def test_theme_sans_fenetre():
    assert window_theme.apply_window_theme(None, GREEN) is False


@pytest.mark.parametrize("palette", [None, {}, {**GREEN, "background": "red"},
                                      {**GREEN, "text": "#fff"}, {**GREEN, "dark": "oui"},
                                      {**GREEN, "icon": "data:image/png;base64,invalide"},
                                      {**GREEN, "icon": "data:image/png;base64," + base64.b64encode(b"pas une image").decode()}])
def test_palette_invalide_ignoree(palette):
    assert window_theme.apply_window_theme(SimpleNamespace(native=None), palette) is False


@pytest.fixture
def native_window():
    if os.name != "nt":
        pytest.skip("WinForms disponible uniquement sous Windows")
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Drawing import Size
    from System.Windows.Forms import Form

    form = Form()
    form.Size = Size(640, 480)
    form.CreateControl()
    yield SimpleNamespace(native=form)
    form.Dispose()


def test_fenetre_suit_la_palette(native_window):
    for palette in (GREEN, LIGHT, GREEN):
        assert window_theme.apply_window_theme(native_window, palette) is True
        expected = int(palette["background"][1:], 16)
        foreground = int(palette["text"][1:], 16)
        assert native_window.native.BackColor.ToArgb() & 0xffffff == expected
        assert native_window.native.ForeColor.ToArgb() & 0xffffff == foreground


def test_icone_windows_suit_image_transmise_et_reutilise_le_handle(native_window):
    from System.Drawing import Bitmap, ColorTranslator, Graphics
    from System.Drawing.Imaging import ImageFormat
    from System.IO import MemoryStream

    for color in ("#c3d49d", "#236f7b", "#efb7a7"):
        bitmap = Bitmap(64, 64)
        graphics = Graphics.FromImage(bitmap)
        stream = MemoryStream()
        try:
            graphics.Clear(ColorTranslator.FromHtml(color))
            bitmap.Save(stream, ImageFormat.Png)
            png = bytes(stream.ToArray())
        finally:
            graphics.Dispose()
            bitmap.Dispose()
            stream.Dispose()
        palette = {**GREEN, "icon": "data:image/png;base64," + base64.b64encode(png).decode()}
        assert window_theme.apply_window_theme(native_window, palette) is True
        preview = native_window.native.Icon.ToBitmap()
        try:
            assert preview.GetPixel(32, 32).ToArgb() & 0xffffff == int(color[1:], 16)
        finally:
            preview.Dispose()
        icon = native_window._theme_icon
        assert window_theme.apply_window_theme(native_window, palette) is True
        assert native_window._theme_icon is icon


@pytest.mark.parametrize("size", [16, 32, 64, 128])
def test_icone_embarquee_lisible_aux_tailles_windows(native_window, size):
    from System.Drawing import Icon, Size

    icon = Icon(str(ROOT / "desktop" / "icon.ico"), Size(size, size))
    try:
        assert icon.Width == icon.Height == size
    finally:
        icon.Dispose()


def test_icone_embarquee_contient_aussi_le_format_256_pour_lexplorateur():
    data = (ROOT / "desktop" / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    images = []
    for index in range(count):
        width, height, _, _, _, depth, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        if width == height == 0:
            png = data[offset:offset + length]
            assert depth == 32 and png[:8] == b"\x89PNG\r\n\x1a\n"
            images.append(struct.unpack_from(">II", png, 16))
    assert (256, 256) in images


def test_attributs_du_titre_transmis_a_windows(native_window, monkeypatch):
    window = native_window
    calls = {}

    def set_attribute(_handle, attribute, value, _size):
        calls[attribute] = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32)).contents.value
        return 0

    monkeypatch.setattr(ctypes.windll.dwmapi, "DwmSetWindowAttribute", set_attribute)
    for palette in (GREEN, LIGHT):
        assert window_theme.apply_window_theme(window, palette) is True
        assert calls[20] == int(palette["dark"])
        assert calls[38] == 1  # fond opaque assorti, sans Mica système
        for attr, key in ((34, "border"), (35, "background"), (36, "text")):
            rgb = palette[key][1:]
            assert calls[attr] == int(rgb[4:6] + rgb[2:4] + rgb[0:2], 16)
