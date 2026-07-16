"""
Minecraft server platform definitions and helpers.
"""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    FABRIC = "fabric"
    NEOFORGE = "neoforge"
    SPIGOT = "spigot"
    PAPER = "paper"
    PURPUR = "purpur"


ALL_PLATFORMS: tuple[Platform, ...] = (
    Platform.FABRIC,
    Platform.NEOFORGE,
    Platform.SPIGOT,
    Platform.PAPER,
    Platform.PURPUR,
)

PLATFORM_LABELS: dict[Platform, str] = {
    Platform.FABRIC: "Fabric",
    Platform.NEOFORGE: "NeoForge",
    Platform.SPIGOT: "Spigot",
    Platform.PAPER: "Paper",
    Platform.PURPUR: "Purpur",
}


def normalize_platform(value: str | Platform | None) -> Platform:
    """Return a valid platform, defaulting to Fabric for unknown/missing values."""
    if isinstance(value, Platform):
        return value
    raw = str(value or "").strip().lower()
    try:
        return Platform(raw)
    except ValueError:
        return Platform.FABRIC


def platform_label(platform: str | Platform | None) -> str:
    return PLATFORM_LABELS.get(normalize_platform(platform), "Fabric")


def is_mod_platform(platform: str | Platform | None) -> bool:
    return normalize_platform(platform) in {Platform.FABRIC, Platform.NEOFORGE}


def is_plugin_platform(platform: str | Platform | None) -> bool:
    return normalize_platform(platform) in {Platform.SPIGOT, Platform.PAPER, Platform.PURPUR}


def content_dir_name(platform: str | Platform | None) -> str:
    return "mods" if is_mod_platform(platform) else "plugins"


def modrinth_loader(platform: str | Platform | None) -> str:
    """Modrinth category/loader facet for mods and modpacks."""
    return normalize_platform(platform).value


def modrinth_plugin_categories(platform: str | Platform | None) -> list[str]:
    """Modrinth plugin categories to OR together when searching plugins."""
    plat = normalize_platform(platform)
    if plat == Platform.PAPER:
        return ["paper", "spigot", "bukkit"]
    if plat == Platform.PURPUR:
        return ["purpur", "paper", "spigot", "bukkit"]
    if plat == Platform.SPIGOT:
        return ["spigot", "bukkit"]
    return [plat.value]


def supports_modpacks(platform: str | Platform | None) -> bool:
    return is_mod_platform(platform)


def supports_optimisation_mods(platform: str | Platform | None) -> bool:
    return normalize_platform(platform) == Platform.FABRIC


def loader_row_title(platform: str | Platform | None) -> str:
    plat = normalize_platform(platform)
    if plat in {Platform.FABRIC, Platform.NEOFORGE}:
        return _("{} loader").format(platform_label(plat))
    if plat in {Platform.PAPER, Platform.PURPUR}:
        return _("Build")
    if plat == Platform.SPIGOT:
        return _("Build method")
    return _("Loader")


def server_subtitle(platform: str | Platform | None, mc_version: str) -> str:
    label = platform_label(platform)
    mc = str(mc_version or "").strip()
    if mc:
        return f"{label} {mc}"
    return label
