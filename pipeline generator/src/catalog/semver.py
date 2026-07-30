"""Just enough version-range matching to pick the right package version.

The catalog states ranges (``^2.1.7``, ``>=1.0.0``), but a harvest has
to pin one concrete version — the shape it freezes must be the shape
that the range will actually resolve to at image build time. Taking
``dist-tags.latest`` instead would silently harvest a major version the
catalog does not allow.

Scope is deliberately small: the operators the RDF-Connect ecosystem
actually publishes with, over ``MAJOR.MINOR.PATCH[-prerelease]``
versions. It is not a general semver implementation — no ``||`` unions,
no hyphen ranges, no build metadata ordering. Anything unrecognised
raises rather than guessing.
"""

from __future__ import annotations

import re

_VERSION = re.compile(
    r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-.]?(?P<pre>(?:a|b|rc|alpha|beta|dev|post)[\w.]*))?"
)

# Ordering rank for prerelease identifiers. Numeric identifiers compare
# numerically; alphabetic ones by this rank, so 1.0.0-alpha < 1.0.0-beta
# < 1.0.0-rc < 1.0.0. Mirrors both semver and PEP 440 closely enough for
# the versions in play.
_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3, "c": 3}


def parse(text: str) -> tuple[int, int, int, tuple]:
    """Parse a version string into a sortable tuple.

    The fourth element orders prereleases before the corresponding
    release: a release gets ``(1,)`` and a prerelease ``(0, rank, n)``,
    so plain tuple comparison puts ``1.0.0-rc.1`` below ``1.0.0``.
    """
    match = _VERSION.match(text.strip())
    if match is None:
        raise ValueError(f"cannot parse version {text!r}")
    major, minor, patch = (int(g or 0) for g in match.group(1, 2, 3))
    pre = match.group("pre")
    if pre is None:
        return major, minor, patch, (1,)

    tokens = re.findall(r"[a-z]+|\d+", pre.lower())
    key: list[int] = [0]
    for token in tokens:
        key.append(int(token) if token.isdigit() else _PRE_RANK.get(token, 0))
    return major, minor, patch, tuple(key)


def _bump_caret(major: int, minor: int, patch: int) -> tuple[int, int, int]:
    """Upper bound (exclusive) of an npm caret range.

    npm tightens the caret as the leading zeros grow: ``^1.2.3`` allows
    the whole 1.x line, ``^0.2.3`` only 0.2.x, and ``^0.0.3`` only that
    single patch. Reproduced exactly, because ``^0.0.1-alpha.2`` is a
    real entry in the catalog.
    """
    if major > 0:
        return major + 1, 0, 0
    if minor > 0:
        return 0, minor + 1, 0
    return 0, 0, patch + 1


def satisfies(version: str, spec: str) -> bool:
    """Does ``version`` fall inside range ``spec``?

    Prereleases are admitted only when ``spec`` itself names a
    prerelease on the same MAJOR.MINOR.PATCH — the npm rule, which keeps
    ``^1.2.0`` from silently resolving to a ``2.0.0-alpha``.
    """
    spec = spec.strip()
    if spec in ("", "*", "latest"):
        return parse(version)[3] == (1,)

    candidate = parse(version)

    def _prerelease_allowed(bound: str) -> bool:
        lower = parse(bound)
        return candidate[3] == (1,) or (
            lower[3] != (1,) and candidate[:3] == lower[:3]
        )

    if spec[0] in "^~":
        base = spec[1:]
        major, minor, patch, _ = parse(base)
        if not _prerelease_allowed(base):
            return False
        upper = (
            _bump_caret(major, minor, patch)
            if spec[0] == "^"
            else (major, minor + 1, 0)
        )
        return parse(base) <= candidate and candidate[:3] < upper

    match = re.match(r"^(>=|<=|==|=|>|<)\s*(.+)$", spec)
    if match is None:
        # Bare version: exact match.
        return candidate == parse(spec)

    operator, bound_text = match.groups()
    bound = parse(bound_text)
    if operator in (">=", ">", "==", "=") and not _prerelease_allowed(bound_text):
        return False
    return {
        ">=": candidate >= bound,
        ">": candidate > bound,
        "<=": candidate <= bound,
        "<": candidate < bound,
        "==": candidate == bound,
        "=": candidate == bound,
    }[operator]


def best_match(versions: list[str], spec: str | None) -> str:
    """Highest version in ``versions`` satisfying ``spec``.

    Raises:
        LookupError: when nothing satisfies the range. Failing here is
            the point — it means the catalog pins a range that the
            registry can no longer serve, which is exactly the drift
            this package exists to surface.
    """
    if spec is None:
        candidates = [v for v in versions if parse(v)[3] == (1,)] or versions
    else:
        candidates = [v for v in versions if satisfies(v, spec)]
    if not candidates:
        raise LookupError(
            f"no published version satisfies {spec!r} "
            f"(available: {', '.join(sorted(versions, key=parse)[-8:])})"
        )
    return max(candidates, key=parse)
