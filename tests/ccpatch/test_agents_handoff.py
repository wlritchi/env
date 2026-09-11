"""Run native agents configuration and handoff fragments without live services."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from wlrenv.ccpatch.agents_handoff import (
    _GETTER,
    _HANDOFF,
    _PARSER,
    _SERIALIZER,
    _handoff,
    agents_view_handoff,
)
from wlrenv.ccpatch.patches import DEV_CHANNEL_INHERITANCE, PatchError

_ROOT = Path(__file__).resolve().parents[2] / "build/sweep-resume"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.index("function ", start + 9)
    return source[start:end].split("var ", 1)[0]


@pytest.mark.parametrize("version", ["2.1.182", "2.1.193", "2.1.195", "2.1.203"])
def test_native_agents_handoff(version: str) -> None:
    path = _ROOT / version / "linux-x64/original.js"
    if not path.exists():
        pytest.skip("native capture unavailable")
    original = path.read_text()
    version_tuple = tuple(map(int, version.split(".")))
    source = DEV_CHANNEL_INHERITANCE.apply(original)
    patched = agents_view_handoff(version_tuple).apply(source)
    match = _HANDOFF.search(source)
    assert match is not None
    getter = _GETTER.search(source)
    parser = _PARSER.search(source)
    serializer = _SERIALIZER.search(source)
    assert getter and parser and serializer
    body = _handoff(match)
    gate = re.match(r"[\w$]+", body)
    assert gate is not None
    fallback = re.search(r'return ([\w$]+)\(\{args:', body)
    logger = re.search(r'catch\([\w$]+\)\{([\w$]+)\(', body)
    accessibility = re.search(r'\.\.\.([\w$]+)\(\)\}\}\)$', body)
    assert fallback and logger and accessibility
    defaults_serializer = re.search(r'\.\.\.([\w$]+)\([\w$]+\)', match.group("middle"))
    functions = (
        _function(source, parser["parser"])
        + _function(source, serializer["serializer"])
        + _function(patched, "_ccAgentsDispatchArgs")
    )
    registration_patch = DEV_CHANNEL_INHERITANCE.patches[-1]
    registration = registration_patch.pattern.search(original)
    assert registration is not None
    block = registration_patch.pattern.sub(
        registration_patch.replacement, registration[0]
    )
    block = block[block.index('if(process.env.CLAUDE_CODE_SESSION_KIND') :]
    functions += (
        f'function registerFromWorker({registration[5]}){{let {registration[2]}=[];'
        f'function {registration[3]}(specs){{return specs.map(name=>({{name}}))}}'
        f'let entries=[];function {registration[4]}(specs){{entries=specs}}'
        + block
        + ';return entries}'
    )
    declarations = (
        f'let saved=[];function {getter["getter"]}(){{return saved}};'
        f'let enabled=true;function {gate[0]}(){{return enabled}};'
        f'async function {match["mount"]}(job,load,options){{return options}};'
        f'function {fallback[1]}(options){{return options}};'
        f'function {logger[1]}(){{}};function {accessibility[1]}(){{return {{}}}};'
    )
    if defaults_serializer:
        declarations += f'function {defaults_serializer[1]}(){{return []}};'
    defaults = match["defaults"]
    arguments = [match["job"], match["load"]] + ([defaults] if defaults else [])
    script = (
        functions
        + declarations
        + (
            f'async function launch({",".join(arguments)}){{if({body}}}'
            + '''
(async()=>{
 const assert=require("node:assert/strict");
 for(const flagged of [false,true]){
  process.argv=["node","claude",...(flagged?["--dangerously-load-development-channels","plugin:notifications"]:[]),"--channels","plugin:approved"];
  saved=["--settings","/normalized/settings.json","--plugin-dir","/normalized/plugins",
    "--add-dir","/normalized/project","--strict-mcp-config","--channels","plugin:approved",
    "--allow-dangerously-skip-permissions","--disable-slash-commands","--fallback-model","secret"];
  for(const inProcess of [false,true]){
   enabled=inProcess;
   const result=await launch("job",null,{});
   const args=inProcess?result.dispatchExtraArgs:result.args.slice(1);
   assert.deepEqual(args,["--settings","/normalized/settings.json","--plugin-dir","/normalized/plugins",
    "--add-dir","/normalized/project","--strict-mcp-config",
    ...(flagged?["--dangerously-load-development-channels","plugin:notifications"]:[]),
    "--channels","plugin:approved"]);
   const index=args.indexOf("--dangerously-load-development-channels");
   process.env.CLAUDE_CODE_SESSION_KIND="bg";
   process.env.CLAUDE_DEV_CHANNELS="plugin:stale";
   assert.deepEqual(registerFromWorker(index<0?[]:[args[index+1]]),
     flagged?[{name:"plugin:notifications",dev:true}]:[]);
   process.env.CLAUDE_CODE_SESSION_KIND="interactive";
   assert.deepEqual(registerFromWorker(["plugin:notifications"]),[]);
  }
 }
 saved=["--channels","plugin:stale","--dangerously-load-development-channels","plugin:stale"];process.argv=["node","claude"];
 enabled=true;assert.deepEqual((await launch("job",null,{})).dispatchExtraArgs,[]);
 enabled=false;assert.deepEqual((await launch("job",null,{})).args,["agents"]);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        )
    )
    runtime = shutil.which("node") or shutil.which("bun")
    assert runtime is not None
    result = subprocess.run(  # noqa: S603
        [runtime, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


def test_handoff_rejects_unknown_source() -> None:
    with pytest.raises(PatchError):
        agents_view_handoff((2, 1, 203)).apply("unknown")
