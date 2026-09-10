"""Run captured native Bash prompt serialization without starting Claude or the SDK."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from wlrenv.ccpatch.patches import MULTI_PROVIDER_SDK

_ID = r"[A-Za-z_$][\w$]*"
_ROOT = Path(os.environ.get("CCPATCH_NATIVE_SOURCE_ROOT", "/tmp/ccpatch-sweep-2.1.182"))  # noqa: S108 - read-only captured release sources
_ANCHORS = [
    r"Co-Authored-By: \$\{[^}]+\} <noreply@anthropic\.com>",
    r"if\([^;]+CLAUDE_CODE_SUPPRESS_SESSION_ATTRIBUTION\)return null",
    r"commit:[^?]+\?`\$\{[^}]+\}\nClaude-Session:",
    r"if\([^;]+\)return [\w$]+\([\w$]+\);let [\w$]+=[\w$]+\(\),[\w$]+=\[\.\.\.",
    r"let [\w$]+=[\w$]+\(\)!==null,[\w$]+=[\w$]+\([\w$]+\),",
    r"- Interactive flags \(",
    r"# Committing changes with git",
    r'"inputJSONSchema"in [\w$]+&&[\w$]+\.inputJSONSchema\?`',
    r"if\(![\w$]+\(\)\)return [\w$]+\.prompt\([\w$]+\);",
]


def _function(source: str, position: int) -> tuple[str, str]:
    matches = list(re.finditer(rf"(?:async )?function ({_ID})\(", source[:position]))
    match = matches[-1]
    following = re.search(rf"(?:async )?function {_ID}\(", source[position:])
    assert following is not None
    text = source[match.start() : position + following.start()]
    text = re.split(r"\}var \w", text, maxsplit=1)[0]
    if not text.endswith("}"):
        text += "}"
    return match[1], text


def _captures(source: str) -> list[tuple[str, str]]:
    captures: list[tuple[str, str]] = []
    for anchor in _ANCHORS:
        matches = list(re.finditer(anchor, source))
        assert len(matches) == 1, (anchor, len(matches))
        captures.append(_function(source, matches[0].start() + 1))
    base = captures[0][0]
    wrapper = re.search(
        rf"function ({_ID})\(\)\{{let (?:"
        rf"{_ID}={_ID}\(\),{_ID}={re.escape(base)}\(\)|"
        rf"{_ID}={re.escape(base)}\(\),{_ID}={_ID}\(\));",
        source,
    )
    assert wrapper is not None
    captures.append(_function(source, wrapper.end()))
    prompt = re.search(
        r"async prompt\(\{model:[\w$]+,tools:[\w$]+\}\).*?\},isConcurrencySafe", source
    )
    assert prompt is not None
    captures.append(("prompt", prompt[0].removesuffix(",isConcurrencySafe")))
    return captures


@pytest.mark.parametrize(
    "architecture", ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"]
)
def test_native_cached_bash_attribution(architecture: str, tmp_path: Path) -> None:
    runtime = shutil.which("node") or shutil.which("bun")
    path = _ROOT / architecture / "original.js"
    reference = Path(
        os.environ.get(
            "CCPATCH_NATIVE_BASELINE",
            "/tmp/ccpatch-sweep-2.1.182/linux-x64/original.js",  # noqa: S108 - read-only harness baseline
        )
    )
    if runtime is None or not path.is_file() or not reference.is_file():
        pytest.skip(
            "requires node/bun, CCPATCH_NATIVE_SOURCE_ROOT pristine sources, "
            "and the .182 Linux x64 harness baseline"
        )
    source = path.read_text()
    originals = _captures(source)
    canonical = _captures(reference.read_text())
    names: dict[str, str] = {}
    for index, ((_, original), (_, baseline)) in enumerate(
        zip(originals, canonical, strict=True)
    ):
        if index == len(_ANCHORS):
            base = originals[0][0]
            original = re.sub(
                rf"let ({_ID}={re.escape(base)}\(\)),({_ID}={_ID}\(\));",
                r"let \2,\1;",
                original,
            )
        # Normalize prompt prose only for the identifier comparison.
        original = original.replace(
            "and for a completed change, per the pre-ship gate below",
            "and for a completed change heading to a PR, only after the pre-ship checks below",
        )
        tokens = re.findall(_ID, original)
        baseline_tokens = re.findall(_ID, baseline)
        assert len(tokens) == len(baseline_tokens)
        for token, baseline_token in zip(tokens, baseline_tokens, strict=True):
            if token != baseline_token:
                assert names.setdefault(token, baseline_token) == baseline_token
    patched = MULTI_PROVIDER_SDK.apply(source)
    functions: list[str] = []
    for name, original in originals:
        if name == "prompt":
            start = patched.index(
                original[: original.index("}")] + ",_ccAttributionSnapshot"
            )
            text = patched[start : patched.index(",isConcurrencySafe", start)]
            functions.append(
                "const bash={name:'Bash',inputJSONSchema:{type:'object'}," + text + "};"
            )
        else:
            declaration = re.search(
                rf"(?:async )?function {re.escape(name)}\(", patched
            )
            assert declaration is not None
            functions.append(_function(patched, declaration.end())[1])
            inner = re.search(rf"async function {re.escape(name)}_ccInner\(", patched)
            if inner is not None:
                functions.append(_function(patched, inner.end())[1])
    helper_start = patched.index("const _ccMultiProviderDefinitions=")
    helper_end = patched.index("function ", helper_start)
    # The helper block ends at the first original function after its injected declarations.
    next_original = re.search(
        r"(?:async )?function (?!_cc)[\w$]+\(", patched[helper_end:]
    )
    assert next_original is not None
    helper = patched[helper_start : helper_end + next_original.start()]

    def normalize(text: str) -> str:
        # Keep model ID prefixes intact when a minified identifier is also a word.
        return re.sub(
            rf"{_ID}(?![\w$]|-Authored-By|-\d)",
            lambda match: names.get(match[0], match[0]),
            text,
        )

    payload = tmp_path / "native.json"
    payload.write_text(
        json.dumps({"source": normalize(helper + "\n" + "\n".join(functions))})
    )
    result = subprocess.run(  # noqa: S603 - local runtime and regression harness
        [
            runtime,
            str(Path(__file__).with_name("native_attribution_regression.mjs")),
            str(payload),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
