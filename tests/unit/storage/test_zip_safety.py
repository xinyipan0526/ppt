import stat
from pathlib import PurePosixPath
from zipfile import ZipInfo

import pytest

from ppt_agent.storage.zip_safety import UnsafeZipEntry, validate_zip_members


def test_validate_zip_members_normalizes_safe_files_and_directories() -> None:
    members = validate_zip_members(
        [ZipInfo("slides/"), ZipInfo("slides/page-1.png")]
    )

    assert tuple(member.path for member in members) == (
        PurePosixPath("slides"),
        PurePosixPath("slides/page-1.png"),
    )
    assert tuple(member.is_directory for member in members) == (True, False)


@pytest.mark.parametrize(
    "name",
    (
        "",
        ".",
        "./file.txt",
        "../outside.txt",
        "folder/../../outside.txt",
        r"..\outside.txt",
        "/absolute.txt",
        r"\rooted.txt",
        "C:/drive.txt",
        r"C:\drive.txt",
        r"\\server\share\file.txt",
    ),
)
def test_validate_zip_members_rejects_unsafe_names(name: str) -> None:
    member = ZipInfo("placeholder")
    member.filename = name

    with pytest.raises(UnsafeZipEntry):
        validate_zip_members([member])


def test_validate_zip_members_rejects_nul_in_name() -> None:
    member = ZipInfo("placeholder")
    member.filename = "safe\0hidden"

    with pytest.raises(UnsafeZipEntry, match="NUL"):
        validate_zip_members([member])


def test_validate_zip_members_rejects_duplicate_normalized_destination() -> None:
    with pytest.raises(UnsafeZipEntry, match="duplicate"):
        validate_zip_members([ZipInfo("folder/file.txt"), ZipInfo(r"folder\file.txt")])


def test_validate_zip_members_rejects_windows_case_aliases() -> None:
    with pytest.raises(UnsafeZipEntry, match="duplicate"):
        validate_zip_members([ZipInfo("slides/Page.png"), ZipInfo("slides/page.png")])


def test_validate_zip_members_rejects_unix_symlink() -> None:
    member = ZipInfo("linked-file")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16

    with pytest.raises(UnsafeZipEntry, match="symbolic link"):
        validate_zip_members([member])
