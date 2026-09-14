"""Opt-in byte equivalence against the pre-modernization patch catalog.

Run with CCPATCH_CAPTURE_DIR=/absolute/path/to/build/sweep-resume. The default
manifest requires every captured platform below, including baseline-unsupported
builds. CCPATCH_CAPTURE_CASES can request a comma-separated version/platform
subset. Missing requested inputs fail. No captures are downloaded.

The independent baseline is read from git object 07eaa7c, not the working tree.
Baseline failures are reported as warnings and in build/capture-equivalence.json;
only baseline-supported inputs can establish output equivalence.
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

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = "07eaa7c6deafc65cce639787375dc0d3f4081bf4"
_PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64")
_CAPTURE_CASES = (
    "2.1.182/linux-x64",
    "2.1.185/linux-x64",
    *(
        f"2.1.{version}/{platform}"
        for version in (186, 187, 190, 191, 193, 195, 196, 197, 198, 199)
        for platform in _PLATFORMS
    ),
)


def _baseline_module() -> ModuleType:
    result = subprocess.run(  # noqa: S603 - fixed git command and repository
        ["git", "-C", str(_ROOT), "show", f"{_BASELINE}:src/wlrenv/ccpatch/patches.py"],  # noqa: S607
        capture_output=True,
        check=True,
    )
    scratch = _ROOT / "build/capture-equivalence"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / "baseline_patches.py"
    path.write_bytes(result.stdout)
    spec = importlib.util.spec_from_file_location("_ccpatch_capture_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _apply(module: ModuleType, source: str, version: tuple[int, ...]) -> bytes:
    for patch_set in module.default_patch_sets(version):
        if patch_set.applies_to(version):
            source = patch_set.apply(source)
    return source.encode("utf-8")


def test_captured_output_matches_independent_baseline() -> None:
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

    baseline = _baseline_module()
    report: dict[str, object] = {"baseline": _BASELINE, "captures": {}}
    captures: dict[str, dict[str, str]] = {}
    report["captures"] = captures
    unsupported: list[str] = []
    failures: list[str] = []
    supported = 0
    report_path = _ROOT / "build/capture-equivalence.json"
    for case in cases:
        original = (root / case / "original.js").read_bytes()
        source = original.decode("utf-8")
        version = patches.parse_version(case.split("/")[0])
        entry = {"input_sha256": hashlib.sha256(original).hexdigest()}
        captures[case] = entry
        try:
            expected = _apply(baseline, source, version)
        except baseline.PatchError as error:
            entry["baseline_error"] = str(error)
            unsupported.append(f"{case}: {error}")
            continue
        supported += 1
        entry["baseline_sha256"] = hashlib.sha256(expected).hexdigest()
        try:
            actual = _apply(patches, source, version)
        except patches.PatchError as error:
            entry["current_error"] = str(error)
            failures.append(f"{case}: {error}")
            continue
        entry["current_sha256"] = hashlib.sha256(actual).hexdigest()
        if actual != expected:
            failures.append(f"{case}: output bytes differ ({entry})")
        else:
            entry["status"] = "byte-identical"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    if unsupported:
        warnings.warn(
            "Baseline-unsupported captures (not equivalence-validated):\n"
            + "\n".join(unsupported),
            stacklevel=1,
        )
    assert supported, f"no baseline-supported captures; see {report_path}"
    assert not failures, "\n".join(failures)
