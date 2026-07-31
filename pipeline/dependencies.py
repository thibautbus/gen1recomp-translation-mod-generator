"""Pinned archive dependency downloads used by the frozen Windows build."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import zipfile


class DependencyError(RuntimeError):
    """An archive failed authenticity or extraction checks."""


def _assert_https_response(response) -> None:
    final_url = getattr(response, "geturl", lambda: "")()
    if final_url and not str(final_url).lower().startswith("https://"):
        raise DependencyError("dependency redirect did not remain HTTPS")


def _tree_digest(root: Path, prefixes: tuple[str, ...] = ()) -> str:
    base = root / prefixes[0] if len(prefixes) == 1 and (root / prefixes[0]).is_dir() else root
    digest = hashlib.sha256()
    files = (p for p in base.rglob("*") if p.is_file() and p.name != ".archive-marker.json")
    # Sort relative path components explicitly.  Path ordering is
    # case-insensitive on Windows but case-sensitive on POSIX, while the
    # component-wise ordering preserves the historical POSIX digest.
    for path in sorted(files, key=lambda item: item.relative_to(base).parts):
        relative = path.relative_to(base).as_posix()
        if path.name == ".verified-archive.zip" or (prefixes and base == root and not any(relative == p or relative.startswith(p + "/") for p in prefixes)):
            continue
        digest.update(relative.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (not path.parts or "." in path.parts or path.is_absolute() or ".." in path.parts
            or any(":" in part for part in path.parts)
            or any(part.rstrip(" .").upper().split(".", 1)[0] in reserved or part != part.rstrip(" .") for part in path.parts)):
        raise DependencyError(f"unsafe archive path: {name}")
    return normalized


def _extract_archive(archive: Path, destination: Path, selective_prefix: str | None) -> None:
    try:
        # Keep the archive open for the complete validation/extraction scope.
        # In particular, ``infolist``/``testzip`` can fail before the old
        # explicit ``finally`` was reached, leaving the handle open on
        # Windows and preventing the caller from replacing/removing the
        # temporary download.
        source = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise DependencyError(f"invalid dependency archive: {archive}") from exc
    with source:
        try:
            entries = source.infolist()
        except (OSError, zipfile.BadZipFile) as exc:
            raise DependencyError(f"invalid dependency archive: {archive}") from exc
        seen: set[str] = set()
        extracted = 0
        if source.testzip() is not None:
            raise DependencyError("dependency archive CRC check failed")
        names = [_safe_name(entry.filename) for entry in entries]
        prefixes = {name.split("/", 1)[0] for name in names if name}
        if len(prefixes) != 1:
            raise DependencyError("archive must contain exactly one top-level prefix")
        expected_prefix = next(iter(prefixes))
        for entry, name in zip(entries, names):
            unix_mode = (entry.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise DependencyError(f"symbolic links are not extracted: {name}")
            if not name or name.endswith("/"):
                continue
            key = name.casefold()
            if key in seen:
                raise DependencyError(f"duplicate archive entry: {name}")
            seen.add(key)
            relative = name
            if expected_prefix and relative.startswith(expected_prefix + "/"):
                relative = relative[len(expected_prefix) + 1 :]
            if selective_prefix:
                selected = selective_prefix.strip("/")
                if relative != selected and not relative.startswith(selected + "/"):
                    continue
            if not relative:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(entry) as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1
        if selective_prefix and extracted == 0:
            raise DependencyError(f"archive does not contain requested subtree: {selective_prefix}")


def fetch_archive(
    url: str,
    sha256: str,
    destination: str | Path,
    *,
    revision: str = "",
    selective_prefix: str | None = None,
    immutable_prefixes: tuple[str, ...] = (),
    trusted_tree_sha256: str = "",
    opener=None,
) -> Path:
    """Download, verify and atomically publish a pinned archive checkout."""
    injected = opener is not None
    opener = opener or urllib.request.urlopen
    if not injected and not url.lower().startswith("https://"):
        raise DependencyError("dependency URL must use HTTPS")
    if not sha256 or len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256.lower()):
        raise DependencyError("archive SHA-256 pin is missing or invalid")
    destination = Path(destination)
    marker = destination / ".archive-marker.json"
    if trusted_tree_sha256 and len(trusted_tree_sha256) != 64:
        raise DependencyError("invalid trusted source tree SHA-256")
    expected = {"revision": revision, "url": url, "sha256": sha256.lower(), "selective_prefix": selective_prefix or "", "immutable_prefixes": list(immutable_prefixes), "trusted_tree_sha256": trusted_tree_sha256.lower()}
    if destination.is_dir() and marker.is_file():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            archive_copy = destination / ".verified-archive.zip"
            trusted_tree = _tree_digest(destination, immutable_prefixes)
            if immutable_prefixes and archive_copy.is_file():
                probe = Path(tempfile.mkdtemp(prefix=".verify-", dir=destination.parent))
                try:
                    _extract_archive(archive_copy, probe, selective_prefix)
                    trusted_tree = _tree_digest(probe, immutable_prefixes)
                finally:
                    shutil.rmtree(probe, ignore_errors=True)
            if archive_copy.is_file() and hashlib.sha256(archive_copy.read_bytes()).hexdigest() == sha256.lower() and metadata == {**expected, "tree_sha256": trusted_tree} and trusted_tree == _tree_digest(destination, immutable_prefixes) and (not trusted_tree_sha256 or trusted_tree == trusted_tree_sha256.lower()):
                return destination
        except (OSError, ValueError):
            pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_download: Path | None = None
    temp_extract: Path | None = None
    try:
        temp_fd, temp_download_name = tempfile.mkstemp(prefix="dependency-", suffix=".zip", dir=destination.parent)
        temp_download = Path(temp_download_name)
        os.close(temp_fd)
        temp_extract = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
        digest = hashlib.sha256()
        with opener(url) as response, temp_download.open("wb") as output:
            _assert_https_response(response)
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != sha256.lower():
            raise DependencyError(f"archive hash mismatch for {url}")
        _extract_archive(temp_download, temp_extract, selective_prefix)
        if trusted_tree_sha256 and _tree_digest(temp_extract, immutable_prefixes) != trusted_tree_sha256.lower():
            raise DependencyError("trusted immutable source tree digest mismatch")
        shutil.copy2(temp_download, temp_extract / ".verified-archive.zip")
        expected = {**expected, "tree_sha256": _tree_digest(temp_extract, immutable_prefixes)}
        (temp_extract / ".archive-marker.json").write_text(json.dumps(expected, sort_keys=True) + "\n", encoding="utf-8")
        backup = destination.with_name(destination.name + ".old")
        if backup.exists():
            shutil.rmtree(backup)
        had_destination = destination.exists()
        if had_destination:
            os.replace(destination, backup)
        try:
            os.replace(temp_extract, destination)
        except Exception:
            if had_destination and backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return destination
    except Exception:
        if temp_extract is not None:
            shutil.rmtree(temp_extract, ignore_errors=True)
        raise
    finally:
        if temp_download is not None:
            temp_download.unlink(missing_ok=True)


def fetch_files(
    base_url: str,
    manifest: dict[str, str],
    destination: str | Path,
    *,
    revision: str = "",
    opener=None,
) -> Path:
    """Fetch a small pinned file manifest and publish it atomically."""
    injected = opener is not None
    opener = opener or urllib.request.urlopen
    if not injected and not base_url.lower().startswith("https://"):
        raise DependencyError("dependency URL must use HTTPS")
    if not manifest:
        raise DependencyError("dependency file manifest is empty")
    for relative, digest in manifest.items():
        _safe_name(relative)
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise DependencyError(f"invalid SHA-256 pin for {relative}")
    destination = Path(destination)
    expected = {"revision": revision, "base_url": base_url.rstrip("/"), "files": dict(sorted((k, v.lower()) for k, v in manifest.items()))}
    marker = destination / ".archive-marker.json"
    if destination.is_dir() and marker.is_file():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file() and p.name not in {".archive-marker.json"}}
            valid = actual == set(manifest) and all(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest.lower() for path, digest in ((destination / key, value) for key, value in manifest.items()))
            if valid and metadata == {**expected, "tree_sha256": _tree_digest(destination)}:
                return destination
        except (OSError, ValueError):
            pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    backup = destination.with_name(destination.name + ".old")
    try:
        for relative, digest in manifest.items():
            target = temp_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"{base_url.rstrip('/')}/{relative}"
            computed = hashlib.sha256()
            with opener(url) as response, target.open("xb") as output:
                _assert_https_response(response)
                while chunk := response.read(1024 * 1024):
                    computed.update(chunk)
                    output.write(chunk)
            if computed.hexdigest() != digest.lower():
                raise DependencyError(f"file hash mismatch for {url}")
        expected = {**expected, "tree_sha256": _tree_digest(temp_root)}
        (temp_root / ".archive-marker.json").write_text(json.dumps(expected, sort_keys=True) + "\n", encoding="utf-8")
        if backup.exists():
            shutil.rmtree(backup)
        had_destination = destination.exists()
        if had_destination:
            os.replace(destination, backup)
        try:
            os.replace(temp_root, destination)
        except Exception:
            if had_destination and backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return destination
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
