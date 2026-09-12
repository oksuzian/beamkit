import importlib
import os
import sys
from pathlib import Path

import pytest

from beamkit import nersc_config as nc

GOOD = """
api = "https://api.iri.nersc.gov/api/v2"
sfapi_dir = "{sfapi}"
account = "m4599"
base_dir = "/global/cfs/cdirs/m4599/Users/u/beamkit"
qos = "regular"
owner = "u"
"""


@pytest.fixture
def sfapi(tmp_path):
    d = tmp_path / "sfapi"
    d.mkdir()
    (d / "client_id").write_text("abcdefghijklm\n")
    key = d / "priv_key.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o400)
    return d


def _write(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / "nersc.toml").write_text(text)


def test_load_good(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    cfg = nc.load(beamkit_home)
    assert cfg.account == "m4599" and cfg.owner == "u" and cfg.procs_per_node == 128
    assert cfg.image.endswith("fnal-wn-el9:latest") and cfg.apptainer.endswith("/bin/apptainer")
    assert cfg.key_file() == sfapi / "priv_key.pem" and cfg.client_id() == "abcdefghijklm"
    assert cfg.as_record()["sfapi_dir"] == str(sfapi)


def test_missing_file_lists_every_required_key(beamkit_home):
    with pytest.raises(nc.ConfigError) as e:
        nc.load(beamkit_home)
    for k in nc.REQUIRED:
        assert k in str(e.value)
    assert str(beamkit_home / "nersc.toml") in str(e.value)


def test_missing_key_is_named(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi).replace('qos = "regular"\n', ""))
    with pytest.raises(nc.ConfigError, match="qos"):
        nc.load(beamkit_home)


def test_unknown_key_is_refused(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi) + 'queue = "debug"\n')
    with pytest.raises(nc.ConfigError, match="unknown key 'queue'"):
        nc.load(beamkit_home)


def test_procs_per_node_must_be_a_positive_int(beamkit_home, sfapi):
    _write(beamkit_home, GOOD.format(sfapi=sfapi) + "procs_per_node = 0\n")
    with pytest.raises(nc.ConfigError, match="procs_per_node"):
        nc.load(beamkit_home)


def test_sfapi_dir_expands_tilde(beamkit_home, sfapi, monkeypatch):
    monkeypatch.setenv("HOME", str(sfapi.parent))
    _write(beamkit_home, GOOD.format(sfapi="~/sfapi"))
    assert nc.load(beamkit_home).sfapi_dir == sfapi


def test_key_file_readable_by_others_is_refused(beamkit_home, sfapi):
    (sfapi / "priv_key.pem").chmod(0o644)
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    with pytest.raises(nc.ConfigError, match="chmod 400"):
        nc.load(beamkit_home).key_file()


def test_jwk_key_is_accepted_and_pem_preferred(beamkit_home, sfapi):
    jwk = sfapi / "priv_key.jwk"
    jwk.write_text('{"kty": "RSA", "d": "x"}')
    jwk.chmod(0o400)
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    assert nc.load(beamkit_home).key_file() == sfapi / "priv_key.pem"
    (sfapi / "priv_key.pem").unlink()
    assert nc.load(beamkit_home).key_file() == jwk


def test_no_key_file_is_refused(beamkit_home, sfapi):
    (sfapi / "priv_key.pem").unlink()
    _write(beamkit_home, GOOD.format(sfapi=sfapi))
    with pytest.raises(nc.ConfigError, match="priv_key.pem or priv_key.jwk"):
        nc.load(beamkit_home).key_file()


def test_missing_tomli_still_imports_and_gives_an_actionable_error(beamkit_home, sfapi, monkeypatch):
    """tools.py imports nersc_config at module level unconditionally (so
    get_server_info can report NERSC availability even with no config), and
    the Fermilab launcher runs under a Python 3.10 venv that never needed
    tomli. Neither import may fail just because tomli is absent; only an
    actual load() call should need it, and then with a fix-it message."""
    import beamkit.tools as tools_mod

    monkeypatch.setitem(sys.modules, "tomli", None)
    monkeypatch.setitem(sys.modules, "tomllib", None)
    try:
        importlib.reload(nc)
        importlib.reload(tools_mod)
        _write(beamkit_home, GOOD.format(sfapi=sfapi))
        with pytest.raises(nc.ConfigError, match="tomli"):
            nc.load(beamkit_home)
    finally:
        importlib.reload(nc)
        importlib.reload(tools_mod)


def test_toml_import_guard_is_symmetric_on_py311(monkeypatch):
    """The >=3.11 branch imports stdlib tomllib; if that's ever missing
    (e.g. a stripped-down interpreter) it must convert to ConfigError too,
    not propagate a bare ImportError out of get_server_info()."""
    monkeypatch.setattr(sys, "version_info", (3, 11, 0))
    monkeypatch.setitem(sys.modules, "tomllib", None)
    with pytest.raises(nc.ConfigError, match="tomli"):
        nc._toml()
