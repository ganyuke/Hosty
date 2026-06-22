"""Internationalization (i18n) support for Hosty."""

from __future__ import annotations

import builtins
import gettext
import os
import sys


def _default_localedir() -> str:
    """Return the default locale directory for the current environment."""
    if os.environ.get("FLATPAK_ID"):
        return "/app/share/locale"
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "share", "locale")
    return os.path.join(sys.prefix, "share", "locale")


def setup_gettext(localedir: str | None = None) -> None:
    """Initialize gettext and install _() into builtins."""
    if localedir is None:
        localedir = _default_localedir()

    try:
        gettext.bindtextdomain("hosty", localedir)
        gettext.textdomain("hosty")
    except Exception:
        pass

    builtins._ = gettext.gettext


setup_gettext()
