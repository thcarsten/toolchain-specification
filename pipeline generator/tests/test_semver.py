"""Version-range matching.

These cases are the reason this module exists rather than a
``dist-tags.latest`` lookup: several catalog entries pin caret ranges on
``0.0.x`` and on prereleases, where npm's rules are least intuitive and a
wrong answer silently harvests a shape from the wrong version.
"""

import pytest

from rdfc_catalog_harvest import semver


@pytest.mark.parametrize(
    "version,spec,expected",
    [
        # Caret on a normal major.
        ("2.1.7", "^2.1.7", True),
        ("2.9.0", "^2.1.7", True),
        ("2.1.6", "^2.1.7", False),
        ("3.0.0", "^2.1.7", False),
        # Caret tightens as leading zeros grow.
        ("0.2.4", "^0.2.3", True),
        ("0.3.0", "^0.2.3", False),
        ("0.0.1", "^0.0.1", True),
        ("0.0.2", "^0.0.1", False),
        # Prereleases admitted only on the same major.minor.patch.
        ("0.0.1-alpha.2", "^0.0.1-alpha.2", True),
        ("0.0.1-alpha.3", "^0.0.1-alpha.2", True),
        ("0.0.1-alpha.1", "^0.0.1-alpha.2", False),
        ("0.0.1", "^0.0.1-alpha.2", True),
        ("1.2.0-alpha.1", "^1.2.0-alpha.1", True),
        ("1.5.0", "^1.2.0-alpha.1", True),
        ("2.0.0-alpha.1", "^1.2.0-alpha.1", False),
        # Tilde pins the minor.
        ("1.2.9", "~1.2.3", True),
        ("1.3.0", "~1.2.3", False),
        # Comparators.
        ("1.0.0", ">=1.0.0", True),
        ("2.0.0", ">=1.0.0", True),
        ("0.9.9", ">=1.0.0", False),
        ("0.9.9", "<1.0.0", True),
        # A plain range must not pull in a prerelease.
        ("2.0.0-rc.1", ">=1.0.0", False),
        # Bare version is exact.
        ("1.2.3", "1.2.3", True),
        ("1.2.4", "1.2.3", False),
    ],
)
def test_satisfies(version, spec, expected):
    assert semver.satisfies(version, spec) is expected


def test_prerelease_orders_below_release():
    assert semver.parse("1.0.0-rc.1") < semver.parse("1.0.0")
    assert semver.parse("1.0.0-alpha.1") < semver.parse("1.0.0-beta.1")
    assert semver.parse("1.0.0-beta.1") < semver.parse("1.0.0-rc.1")


def test_best_match_picks_highest_satisfying():
    assert semver.best_match(["1.0.0", "2.1.7", "2.4.0", "3.0.0"], "^2.1.7") == "2.4.0"


def test_best_match_ignores_prereleases_without_opt_in():
    assert semver.best_match(["1.0.0", "2.0.0-rc.1"], None) == "1.0.0"


def test_best_match_raises_when_range_unsatisfiable():
    # A pinned range the registry can no longer serve is drift worth
    # failing the harvest over, not something to paper over.
    with pytest.raises(LookupError, match="no published version satisfies"):
        semver.best_match(["1.0.0", "1.5.0"], "^2.0.0")


def test_unparseable_version_raises():
    with pytest.raises(ValueError, match="cannot parse version"):
        semver.parse("not-a-version")
