"""Directory links into the shared engine checkout."""
from __future__ import annotations

import os
from pathlib import Path


# rom_text_caches() in Modkit reads <checkout>/<edition>/data/generated/gba.
ROM_TEXT_EDITIONS = ("firered", "leafgreen", "ruby", "sapphire", "emerald")


def is_link(path: Path) -> bool:
    """A symlink, or the directory junction a Windows build links with."""
    if path.is_symlink():
        return True
    if hasattr(os.path, "isjunction"):  # Python 3.12
        return os.path.isjunction(path)
    try:
        tag = getattr(os.lstat(path), "st_reparse_tag", 0)
    except OSError:
        return False
    return tag == 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT


def link_directory(link: Path, target: Path) -> None:
    """Point ``link`` at the directory ``target``.

    Windows only lets an elevated prompt or Developer Mode make a symlink, so
    a build there makes a directory junction instead, which needs neither and
    which Modkit reads through the same way.
    """
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    """Remove a link made by link_directory, never what it points at."""
    try:
        link.unlink()
    except OSError:
        if not is_link(link):
            raise
        os.rmdir(link)  # a junction, where unlink refuses a directory


def remove_rom_text_links(checkout: Path) -> list[Path]:
    """Remove the Gen 3 extract links a killed build left in the checkout.

    A build links its extract into the shared engine checkout only while it
    runs (pipeline.gen3.mod.rom_text_cache). One that was killed before its
    cleanup leaves the link behind, and every later Modkit run reads that
    cart's text: a Red/Blue/Yellow scaffold then lists its labels in
    lang/strings.lua, and pack checks other mods against its English. A real
    import directory is never touched.
    """
    removed = []
    for edition in ROM_TEXT_EDITIONS:
        link = Path(checkout) / edition
        if is_link(link):
            unlink_directory(link)
            removed.append(link)
    return removed
