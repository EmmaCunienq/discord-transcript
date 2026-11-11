"""Couleurs du cadre WinForms, à partir de la palette CSS active."""

import ctypes
import base64
import binascii
import logging
import os
import re
import struct
from ctypes import wintypes

logger = logging.getLogger(__name__)


def _set_icon(window, native, png: bytes) -> None:
    """Copie l'icône avant de libérer le HICON temporaire, sans accumuler de handles GDI."""
    if getattr(window, "_theme_icon_png", None) == png:
        return
    from System import Array, Byte
    from System.Drawing import Bitmap, Icon
    from System.IO import MemoryStream

    stream = MemoryStream(Array[Byte](png))
    bitmap = None
    handle = None
    try:
        bitmap = Bitmap(stream)
        handle = bitmap.GetHicon()
        icon = Icon.FromHandle(handle).Clone()
    finally:
        if handle is not None:
            destroy = ctypes.windll.user32.DestroyIcon
            destroy.argtypes = [wintypes.HICON]
            destroy.restype = wintypes.BOOL
            destroy(handle.ToInt64())
        if bitmap is not None:
            bitmap.Dispose()
        stream.Dispose()
    previous = getattr(window, "_theme_icon", None)
    try:
        native.Icon = icon
    except Exception:
        icon.Dispose()
        raise
    window._theme_icon = icon
    window._theme_icon_png = png
    if previous is not None:
        previous.Dispose()


def apply_window_theme(window, palette: dict) -> bool:
    """Applique sur le thread graphique ; les anciennes versions de Windows gardent leur cadre natif."""
    keys = ("background", "text", "border")
    if not isinstance(palette, dict) or any(
        not isinstance(palette.get(key), str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", palette[key])
        for key in keys
    ) or not isinstance(palette.get("dark"), bool):
        return False
    icon = None
    if "icon" in palette:
        data = palette["icon"]
        if not isinstance(data, str) or not data.startswith("data:image/png;base64,") or len(data) > 40000:
            return False
        try:
            icon = base64.b64decode(data.partition(",")[2], validate=True)
        except (ValueError, binascii.Error):
            return False
        if len(icon) < 24 or icon[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(">II", icon[16:24]) != (64, 64):
            return False
    native = getattr(window, "native", None)
    if os.name != "nt" or native is None:
        return False

    # pywebview a déjà chargé ces assemblies avant d'exposer window.native.
    from System import Action
    from System.Drawing import ColorTranslator

    def update():
        if native.IsDisposed:
            return
        colors = {key: ColorTranslator.FromHtml(palette[key]) for key in keys}
        native.BackColor = colors["background"]
        native.ForeColor = colors["text"]
        if icon is not None:
            _set_icon(window, native, icon)
        # Attributs DWM documentés : mode sombre (Win10), couleurs du cadre (Win11).
        dwm_set = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dwm_set.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm_set.restype = ctypes.c_long
        handle = native.Handle.ToInt64()
        values = {20: int(palette["dark"]), 38: 1, 34: colors["border"],
                  35: colors["background"], 36: colors["text"]}
        for attribute, value in values.items():
            # COLORREF Windows est en ordre BGR, avec un octet de poids fort nul.
            number = value if isinstance(value, int) else value.R | (value.G << 8) | (value.B << 16)
            color = ctypes.c_uint32(number)
            dwm_set(handle, attribute, ctypes.byref(color), ctypes.sizeof(color))

    try:
        if native.InvokeRequired:
            native.Invoke(Action(update))
        else:
            update()
    except Exception:
        logger.exception("Impossible d'assortir le cadre de la fenêtre")
        return False
    return True
