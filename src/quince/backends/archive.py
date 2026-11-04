"""Archive conversions: ZIP, TAR, TAR.GZ and 7Z into each other; RAR as input only.

An archive is unpacked into a private folder and packed again in the target format. Before
anything is written, every entry is checked; an archive that tries to escape is refused as a
whole, with the offending name:

- absolute paths, ``..`` components, and (for TAR) links pointing outside the archive or
  device files are refused (TAR uses Python's ``data`` extraction filter);
- for 7Z and RAR the listing is checked first, then the unpacked tree again (no path may
  resolve outside the folder);
- encrypted archives are refused ("password-protected archives are not supported");
- the unpacked size is checked against the free space.

Symbolic links inside the archive are kept, file times and modes too (without set-uid bits).
"""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from quince.backends import files, process
from quince.core.jobs import JobContext, JobError
from quince.core.routes import Route

TIMEOUT_S = 3600


class UnsafeArchive(JobError):
    pass


def check_name(name: str) -> None:
    """Refuse a member name that could land outside the extraction folder."""
    clean = name.replace("\\", "/")
    path = PurePosixPath(clean)
    if clean.startswith("/") or (len(clean) > 1 and clean[1] == ":") or ".." in path.parts:
        raise UnsafeArchive(f"The archive contains an unsafe path ({name}); nothing was extracted")


def check_tree(root: Path) -> None:
    """After extraction: nothing may point outside ``root``."""
    real_root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                target = (path.parent / os.readlink(path)).resolve()
                if target != real_root and real_root not in target.parents:
                    raise UnsafeArchive(
                        f"The archive contains a link leading outside it ({name}); nothing was "
                        "extracted"
                    )


def _check_space(entries_size: int, folder: Path) -> None:
    free = shutil.disk_usage(folder).free
    if entries_size > free:
        raise JobError(
            f"Not enough space to unpack: needs {files.human_size(entries_size)}, "
            f"{files.human_size(free)} free"
        )


# --- unpacking ------------------------------------------------------------------------


def _unzip(ctx: JobContext, src: Path, dest: Path) -> None:
    try:
        archive = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError) as exc:
        raise JobError("Not a readable ZIP archive", str(exc)) from exc
    with archive:
        infos = archive.infolist()
        for info in infos:
            check_name(info.filename)
            if info.flag_bits & 0x1:
                raise JobError("Password-protected archives are not supported")
        _check_space(sum(i.file_size for i in infos), dest)
        for n, info in enumerate(infos):
            ctx.check()
            ctx.progress(0.5 * n / max(len(infos), 1))
            target = dest / info.filename
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(archive.read(info).decode(), target)
                continue
            archive.extract(info, dest)
            if mode and not info.is_dir():
                os.chmod(target, stat.S_IMODE(mode) & 0o777)
            mtime = _zip_time(info)
            if mtime is not None:
                os.utime(target, (mtime, mtime), follow_symlinks=False)


def _zip_time(info: zipfile.ZipInfo) -> float | None:
    import time

    try:
        return time.mktime((*info.date_time, 0, 0, -1))
    except (OverflowError, ValueError):
        return None


def _untar(ctx: JobContext, src: Path, dest: Path) -> None:
    try:
        archive = tarfile.open(src)  # noqa: SIM115 (closed by the with block below)
    except (tarfile.TarError, OSError) as exc:
        raise JobError("Not a readable TAR archive", str(exc)) from exc
    with archive:
        members = archive.getmembers()
        for member in members:
            check_name(member.name)
            try:
                tarfile.data_filter(member, str(dest))
            except tarfile.FilterError as exc:
                raise UnsafeArchive(
                    f"The archive contains an unsafe entry ({member.name}); nothing was extracted"
                ) from exc
        _check_space(sum(m.size for m in members), dest)
        archive.extractall(dest, filter="data")


def _un7z(ctx: JobContext, src: Path, dest: Path) -> None:
    listing: list[str] = []
    result = process.run(
        ["7z", "l", "-slt", "-ba", src], ctx, timeout_s=TIMEOUT_S, on_stdout=listing.append,
        check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise JobError("Not a readable 7Z archive", "\n".join(result.stderr_tail))
    total = 0
    for line in listing:
        key, _, value = line.partition(" = ")
        if key == "Path":
            check_name(value)
        elif key == "Size" and value.isdigit():
            total += int(value)
        elif key == "Encrypted" and value == "+":
            raise JobError("Password-protected archives are not supported")
    _check_space(total, dest)
    process.run(["7z", "x", "-y", "-bd", f"-o{dest}", "-p", src], ctx, timeout_s=TIMEOUT_S)


def _unrar(ctx: JobContext, src: Path, dest: Path) -> None:
    listing: list[str] = []
    result = process.run(
        ["unrar", "lta", "-p-", src], ctx, timeout_s=TIMEOUT_S, on_stdout=listing.append,
        check=False,
    )  # fmt: skip
    if result.returncode != 0:
        raise JobError("Not a readable RAR archive", "\n".join(result.stderr_tail))
    total = 0
    for line in listing:
        key, _, value = line.strip().partition(": ")
        if key == "Name":
            check_name(value)
        elif key == "Size" and value.isdigit():
            total += int(value)
        elif key == "Flags" and "encrypted" in value.lower():
            raise JobError("Password-protected archives are not supported")
    _check_space(total, dest)
    process.run(["unrar", "x", "-y", "-p-", "-o-", src, f"{dest}/"], ctx, timeout_s=TIMEOUT_S)


UNPACK = {"zip": _unzip, "tar": _untar, "targz": _untar, "7z": _un7z, "rar": _unrar}


# --- packing --------------------------------------------------------------------------


def _top_level(root: Path) -> list[str]:
    return sorted(os.listdir(root))


def _zip(ctx: JobContext, root: Path, out: Path) -> None:
    # strict_timestamps=False: times before 1980 (which ZIP cannot store) become 1980-01-01.
    with zipfile.ZipFile(
        out, "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False
    ) as archive:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(dirnames) + sorted(filenames):
                ctx.check()
                path = Path(dirpath) / name
                arcname = path.relative_to(root).as_posix()
                if path.is_symlink():
                    info = zipfile.ZipInfo(arcname)
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, os.readlink(path))
                elif path.is_dir():
                    archive.write(path, arcname + "/")
                else:
                    archive.write(path, arcname)


def _tar(ctx: JobContext, root: Path, out: Path, mode: str) -> None:
    with tarfile.open(out, mode) as archive:
        for name in _top_level(root):
            ctx.check()
            archive.add(root / name, arcname=name)


def _7z(ctx: JobContext, root: Path, out: Path) -> None:
    names = _top_level(root)
    if not names:
        raise JobError("7Z cannot store an empty archive")
    process.run(
        ["7z", "a", "-t7z", "-bd", "-snl", str(out.resolve()), "--", *names],
        ctx, timeout_s=TIMEOUT_S, cwd=root,
    )  # fmt: skip


def pack(ctx: JobContext, root: Path, target: str, out: Path) -> None:
    if target == "zip":
        _zip(ctx, root, out)
    elif target == "tar":
        _tar(ctx, root, out, "w")
    elif target == "targz":
        _tar(ctx, root, out, "w:gz")
    elif target == "7z":
        _7z(ctx, root, out)
    else:
        raise JobError(f"Cannot write {target} archives")


def convert(ctx: JobContext, src: Path, out: Path, route: Route) -> None:
    with tempfile.TemporaryDirectory(prefix="quince-archive-") as tmp:
        root = Path(tmp) / "content"
        root.mkdir()
        ctx.progress(0.0, f"Unpacking {src.name}")
        UNPACK[route.source](ctx, src, root)
        check_tree(root)
        ctx.progress(0.5, f"Packing {out.name.split('-', 2)[-1]}")
        pack(ctx, root, route.target, out)
