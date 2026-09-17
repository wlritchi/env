"""Opt-in byte equivalence against immutable git oracles.

Set CCPATCH_CAPTURE_DIR to the native capture root. CCPATCH_CAPTURE_CASES selects
comma-separated version/platform cases; absent requested captures fail. Set
CCPATCH_EQUIVALENCE_DIR to isolate reports and extracted oracle modules.

The original modernization remains checked against its pre-refactor oracle.
Current output is checked against the completed .272 sweep, which includes later
behavioral fixes. Neither comparison normalizes or ignores output differences.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import warnings
from pathlib import Path
from types import ModuleType

import pytest

from wlrenv.ccpatch import patches
from wlrenv.ccpatch.module_runtime import finalize_module_runtime

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = "07eaa7c6deafc65cce639787375dc0d3f4081bf4"
_REFACTOR = "d77cd8408ee76d66afb182ee563a1fd92e343571"
_MODERN = "17745782ba018b88be671227cf7836da3452caa1"
_PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64")
_HISTORICAL_CASES = (
    "2.1.182/linux-x64",
    "2.1.185/linux-x64",
    *(
        f"2.1.{version}/{platform}"
        for version in (186, 187, 190, 191, 193, 195, 196, 197, 198, 199)
        for platform in _PLATFORMS
    ),
)
_CAPTURE_CASES = (
    *_HISTORICAL_CASES,
    *(
        f"2.1.{version}/{platform}"
        for version in range(200, 212)
        for platform in _PLATFORMS
    ),
    *(
        f"2.1.{version}/linux-x64"
        for version in range(212, 273)
        if version not in (230, 244, 249, 253, 254, 255, 256, 262, 264)
    ),
    *(f"2.1.272/{platform}" for platform in _PLATFORMS if platform != "linux-x64"),
)


def _scratch() -> Path:
    path = Path(
        os.environ.get(
            "CCPATCH_EQUIVALENCE_DIR", str(_ROOT / "build/capture-equivalence")
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _baseline_module(revision: str) -> ModuleType:
    """Load the oracle and its relative imports from the same git tree."""
    directory = _scratch() / revision
    directory.mkdir(parents=True, exist_ok=True)
    package_name = f"_ccpatch_capture_{revision}"
    package = ModuleType(package_name)
    package.__path__ = [str(directory)]
    sys.modules[package_name] = package
    names = (
        ("patches", "agents_handoff", "module_runtime")
        if revision == _MODERN
        else ("patches",)
    )
    for name in names:
        result = subprocess.run(  # noqa: S603 - fixed git command and repository
            [  # noqa: S607
                "git",
                "-C",
                str(_ROOT),
                "show",
                f"{revision}:src/wlrenv/ccpatch/{name}.py",
            ],
            capture_output=True,
            check=True,
        )
        (directory / f"{name}.py").write_bytes(result.stdout)
    spec = importlib.util.spec_from_file_location(
        f"{package_name}.patches", directory / "patches.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _apply(
    module: ModuleType, source: str, version: tuple[int, ...], *, finalize: bool = False
) -> bytes:
    for patch_set in module.default_patch_sets(version):
        if patch_set.applies_to(version):
            source = patch_set.apply(source)
    if finalize:
        if module is patches:
            source = finalize_module_runtime(source)
        else:
            source = sys.modules[
                f"{module.__package__}.module_runtime"
            ].finalize_module_runtime(source)
    return source.encode("utf-8")


def _requested_cases() -> tuple[Path, tuple[str, ...]]:
    capture_dir = os.environ.get("CCPATCH_CAPTURE_DIR")
    if capture_dir is None:
        pytest.skip("capture suite disabled; set CCPATCH_CAPTURE_DIR to opt in")
    assert capture_dir.strip(), "CCPATCH_CAPTURE_DIR must not be empty"
    root = Path(capture_dir)
    assert root.is_dir(), f"capture directory not found: {root}"
    requested = os.environ.get("CCPATCH_CAPTURE_CASES")
    cases = (
        tuple(case.strip() for case in requested.split(","))
        if requested is not None
        else _CAPTURE_CASES
    )
    assert cases and all(cases), "CCPATCH_CAPTURE_CASES must not be empty"
    assert len(cases) == len(set(cases)), "duplicate capture requests"
    for case in cases:
        assert case in _CAPTURE_CASES, f"unknown capture request: {case}"
    missing = [
        str(root / case / "original.js")
        for case in cases
        if not (root / case / "original.js").is_file()
    ]
    assert not missing, f"missing requested captures: {missing}"
    return root, cases


@pytest.mark.parametrize(
    "historical", [True, False], ids=["original-refactor", "current-regression"]
)
def test_captured_output_matches_independent_baseline(historical: bool) -> None:
    root, requested = _requested_cases()
    cases = tuple(
        case for case in requested if not historical or case in _HISTORICAL_CASES
    )
    if not cases:
        return
    revision = _BASELINE if historical else _MODERN
    baseline = _baseline_module(revision)
    candidate = _baseline_module(_REFACTOR) if historical else patches
    captures: dict[str, dict[str, str]] = {}
    report: dict[str, object] = {
        "baseline": revision,
        "candidate": _REFACTOR if historical else "working-tree",
        "captures": captures,
    }
    unsupported: list[str] = []
    failures: list[str] = []
    supported = 0
    report_path = (
        _scratch()
        / f"capture-equivalence-{'historical' if historical else 'current'}.json"
    )
    for case in cases:
        original = (root / case / "original.js").read_bytes()
        source = original.decode("utf-8")
        version = patches.parse_version(case.split("/")[0])
        entry = {"input_sha256": hashlib.sha256(original).hexdigest()}
        captures[case] = entry
        try:
            expected = _apply(baseline, source, version, finalize=not historical)
        except baseline.PatchError as error:
            entry["baseline_error"] = str(error)
            entry["status"] = "baseline-unsupported"
            if historical:
                unsupported.append(f"{case}: {error}")
            else:
                failures.append(f"{case}: modern oracle failed: {error}")
        else:
            supported += 1
            entry["baseline_sha256"] = hashlib.sha256(expected).hexdigest()
            try:
                actual = _apply(candidate, source, version, finalize=not historical)
            except candidate.PatchError as error:
                entry["current_error"] = str(error)
                failures.append(f"{case}: {error}")
            else:
                entry["current_sha256"] = hashlib.sha256(actual).hexdigest()
                entry["status"] = (
                    "byte-identical" if actual == expected else "different"
                )
                if actual != expected:
                    failures.append(f"{case}: output bytes differ ({entry})")
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    if unsupported:
        warnings.warn(
            "Baseline-unsupported captures (not equivalence-validated):\n"
            + "\n".join(unsupported),
            stacklevel=1,
        )
    assert not failures, "\n".join(failures)
    assert supported or (
        historical and os.environ.get("CCPATCH_CAPTURE_CASES") is not None
    ), f"no baseline-supported captures; see {report_path}"
