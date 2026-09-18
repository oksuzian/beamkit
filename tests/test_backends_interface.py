"""Both backends export the interface of the spec's §6, with the same
parameter names. A new site is a module that passes this test."""
import inspect

import pytest

from beamkit import backends
from beamkit.backends import fermilab, nersc

TOOL_SIDE = {
    "available": [],
    "status": ["rec"],
    "outputs": ["rec"],
    "fetch_outputs": ["rec", "dest", "kind"],
    "submit_run": ["rec", "run_as"],
    "make_recoveries": ["rec", "run_as", "confirm"],
    "make_beamfile": ["rec", "flavor", "run_as", "plane", "cuts", "label", "publish", "location", "confirm"],
}


@pytest.mark.parametrize("backend", [fermilab, nersc], ids=["fermilab", "nersc"])
@pytest.mark.parametrize("name,params", TOOL_SIDE.items())
def test_tool_side_interface(backend, name, params):
    fn = getattr(backend, name)
    assert list(inspect.signature(fn).parameters) == params, name


def test_get_returns_the_module_and_refuses_others():
    assert backends.get("fermilab") is fermilab and backends.get("nersc") is nersc
    with pytest.raises(backends.BeamkitError, match="site must be one of"):
        backends.get("perlmutter")


CREATION = {
    "resolve_identity": ["req"],
    "validate": ["req", "ident"],
    "taken": ["ident", "tag"],
    "retryable": ["rec"],
    "new_block": ["req", "ident", "pin", "run_id", "dsconf"],
    "enqueue": ["rec", "req", "ident", "pin", "rdir"],
    "after_failure": ["rec", "ident"],
    "submit": ["rec", "ident", "confirm"],
}


@pytest.mark.parametrize("backend", [fermilab, nersc], ids=["fermilab", "nersc"])
@pytest.mark.parametrize("name,params", CREATION.items())
def test_creation_interface(backend, name, params):
    assert list(inspect.signature(getattr(backend, name)).parameters) == params, name


@pytest.mark.parametrize("backend", [fermilab, nersc], ids=["fermilab", "nersc"])
def test_block_is_a_dataclass(backend):
    import dataclasses
    assert dataclasses.is_dataclass(backend.Block)
