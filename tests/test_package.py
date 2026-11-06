import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hcawn_ha
from hcawn_ha import HAPipeline, demo_registry, pipeline, weights
from hcawn_ha.errors import NativeUnavailable, WeightsNotFound


def test_resolver_unit_and_stress():
    import resolver_checks
    resolver_checks.unit()
    assert resolver_checks.stress(60)["wrong_ok"] == 0          # never a silently wrong device


def test_pipeline_selftest():
    assert pipeline.selftest()


def test_confirm_goes_to_confirm_list():
    p = HAPipeline(None, demo_registry(), 0.7, generate=lambda t: ('[{"name":"HassTurnOn","arguments":{"name":"garage door"}}]', {"min_vt": 0.99}))
    r = p.handle("open the garage door")
    assert not r["execute"] and len(r["confirm"]) == 1


def test_two_commands_in_one_sentence():
    outs = {"turn on the kitchen light": '[{"name":"HassTurnOn","arguments":{"name":"kitchen light"}}]',
            "turn off the bedroom fan": '[{"name":"HassTurnOff","arguments":{"name":"bedroom fan"}}]'}
    p = HAPipeline(None, demo_registry(), 0.7, generate=lambda t: (outs[t], {"min_vt": 0.99}))
    r = p.handle("turn on the kitchen light and turn off the bedroom fan")
    assert len(r["execute"]) == 2


def test_tools_file_has_the_13_tools():
    from hcawn_ha import _engine
    names = [t["name"] for t in _engine.tools()]
    assert len(names) == 13 and "HassLightSet" in names and "HassVacuumReturnToBase" in names


def test_weights_manifest():
    man = weights._manifest()
    assert man["repo"] == "datatab/hcawn-ha" and set(man["files"]) == {"int8", "int4"}
    assert all(len(v["sha256"]) == 64 and v["size"] > 1_000_000 for v in man["files"].values())


def test_missing_weights_message():
    with pytest.raises(WeightsNotFound):
        weights.find_weights("/no/such/file.bin")


def test_pure_install_has_no_engine_but_says_so():
    from hcawn_ha import _native
    if _native.available():
        pytest.skip("this install has the engine")
    with pytest.raises(NativeUnavailable):
        HAPipeline("int8", demo_registry())


def test_cli_selftest_and_info(capsys):
    from hcawn_ha import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["selftest"])
    assert e.value.code == 0
    cli.main(["info"])
    assert "hcawn-ha" in capsys.readouterr().out


def test_demo_module_imports():
    from hcawn_ha.demo import app, house
    assert callable(app.main) and house.registry()
