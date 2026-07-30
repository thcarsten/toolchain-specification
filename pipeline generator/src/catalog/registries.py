"""Fetch a package and pull the RDF-Connect definition out of it.

Three sources, one return shape (:class:`FetchedPackage`):

- ``npm`` — the registry JSON gives the version list, repository URL and
  tarball; the tarball ships the ``processors.ttl`` that ``owl:imports``
  already points at.
- ``pypi`` — same story via the JSON API. The wheel is preferred over
  the sdist because its member paths are the *installed* layout, which
  is what ``owl:imports`` has to encode.
- ``path`` — a checked-out directory in this repo, for components not
  published anywhere.

This is the only module in the package that touches the network.
:mod:`catalog.emitter` reads exclusively from the snapshot written by
:mod:`catalog.harvester`, so regeneration stays offline and
reproducible.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import semver

_USER_AGENT = "dishacled-toolchain-catalog/0.1 (+https://w3id.org/toolchain)"
_TIMEOUT = 30


@dataclass(frozen=True)
class FetchedPackage:
    """A resolved package plus every Turtle file it ships.

    ``turtle_files`` maps a path to its decoded contents. The key is
    already normalised to the form ``owl:imports`` needs — see
    :func:`_npm_member_path` and :func:`_python_member_path` for what
    "normalised" means per ecosystem.
    """

    source: str
    package: str | None
    resolved_version: str | None
    landing_page: str | None
    turtle_files: dict[str, str]


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def _get_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read()


def _clean_repository_url(raw: str | None) -> str | None:
    """Normalise a registry repository field into a browsable https URL.

    Registries store these as git remotes (``git+https://...git``,
    ``git://...``); ``dcat:landingPage`` wants the page a human would
    open.
    """
    if not raw:
        return None
    url = raw.removeprefix("git+").removeprefix("git://")
    url = url.removesuffix(".git")
    if url.startswith("github.com"):
        url = f"https://{url}"
    return url or None


def _npm_member_path(name: str) -> str | None:
    """Package-root-relative path for an npm tarball member.

    npm wraps everything in a top-level ``package/`` directory, which is
    not part of the installed path under ``node_modules/<pkg>/``.
    """
    if not name.endswith(".ttl"):
        return None
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 and parts[0] == "package" else name


def _python_member_path(name: str) -> str | None:
    """site-packages-relative path for a wheel or sdist member.

    Wheel members are already site-packages-relative. Sdist members
    carry a ``<name>-<version>/`` prefix and usually a ``src/`` layout
    directory, neither of which survives installation.
    """
    if not name.endswith(".ttl"):
        return None
    parts = name.split("/")
    if parts and "-" in parts[0] and not parts[0].endswith(".ttl"):
        parts = parts[1:]  # sdist root directory
    if parts and parts[0] == "src":
        parts = parts[1:]  # src-layout directory
    return "/".join(parts) if parts else None


def _extract_turtle(blob: bytes, is_zip: bool, normalise) -> dict[str, str]:
    files: dict[str, str] = {}
    if is_zip:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for name in archive.namelist():
                key = normalise(name)
                if key:
                    files[key] = archive.read(name).decode("utf-8")
        return files

    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(blob))) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            key = normalise(member.name)
            if key is None:
                continue
            handle = archive.extractfile(member)
            if handle is not None:
                files[key] = handle.read().decode("utf-8")
    return files


def fetch_npm(package: str, version_spec: str | None) -> FetchedPackage:
    """Resolve ``package`` against ``version_spec`` and download it."""
    metadata = _get_json(f"https://registry.npmjs.org/{package}")
    versions = list(metadata.get("versions", {}))
    if not versions:
        raise LookupError(f"npm package {package} publishes no versions")
    resolved = semver.best_match(versions, version_spec)
    release = metadata["versions"][resolved]

    landing_page = _clean_repository_url(
        (release.get("repository") or {}).get("url")
        if isinstance(release.get("repository"), dict)
        else release.get("repository")
    ) or release.get("homepage")

    blob = _get_bytes(release["dist"]["tarball"])
    return FetchedPackage(
        source="npm",
        package=package,
        resolved_version=resolved,
        landing_page=landing_page,
        turtle_files=_extract_turtle(blob, is_zip=False, normalise=_npm_member_path),
    )


def fetch_pypi(package: str, version_spec: str | None) -> FetchedPackage:
    """Resolve ``package`` against ``version_spec`` and download it."""
    metadata = _get_json(f"https://pypi.org/pypi/{package}/json")
    versions = [v for v, files in metadata.get("releases", {}).items() if files]
    if not versions:
        raise LookupError(f"PyPI package {package} publishes no files")
    resolved = semver.best_match(versions, version_spec)

    distributions = metadata["releases"][resolved]
    # Wheel first: its member paths are the installed layout, so
    # owl:imports can be built without guessing how the sdist maps onto
    # site-packages.
    chosen = next(
        (d for d in distributions if d.get("packagetype") == "bdist_wheel"),
        next((d for d in distributions if d.get("packagetype") == "sdist"), None),
    )
    if chosen is None:
        raise LookupError(f"PyPI package {package}=={resolved} has no wheel or sdist")

    info = metadata.get("info", {})
    landing_page = _clean_repository_url(
        (info.get("project_urls") or {}).get("Source")
        or (info.get("project_urls") or {}).get("Homepage")
        or info.get("home_page")
    )

    blob = _get_bytes(chosen["url"])
    return FetchedPackage(
        source="pypi",
        package=package,
        resolved_version=resolved,
        landing_page=landing_page,
        turtle_files=_extract_turtle(
            blob,
            is_zip=chosen["url"].endswith(".whl"),
            normalise=_python_member_path,
        ),
    )


def fetch_path(package: str | None, directory: Path) -> FetchedPackage:
    """Read every ``.ttl`` under a checked-out package directory.

    Member keys are normalised the same way as a Python distribution's,
    so a ``src/``-layout local package yields the same paths its
    installed form would.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"tcs:fromPath directory does not exist: {directory}")

    files: dict[str, str] = {}
    for candidate in sorted(directory.rglob("*.ttl")):
        relative = candidate.relative_to(directory).as_posix()
        key = _python_member_path(relative) or relative
        files[key] = candidate.read_text(encoding="utf-8")

    return FetchedPackage(
        source="path",
        package=package,
        resolved_version=None,
        landing_page=None,
        turtle_files=files,
    )
