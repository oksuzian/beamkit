"""fetch_outputs: CFS files into a local directory through the API."""
import pytest

from beamkit import BeamkitError, iri, tools
from tests.test_sfapi import BASE, RD, SHA, sfapi_fake   # noqa: F401


def _run(fake, njobs=3):
    tools.run_beamline(tag="T", run_as="self", deck_ref=SHA, site="nersc", njobs=njobs, events_per_job=10)
    for j in fake.jobs.values():
        j.update(state="COMPLETED", exit_code=0)
    for idx in range(njobs):
        seq = f"{idx:08d}"
        fake.files[f"{RD}/out/log.u.T.e470313.{seq}.log"] = b"BK_DONE\n"
        fake.files[f"{RD}/out/nts.u.T.e470313.{seq}.root"] = bytes([idx]) * (100 + idx)


def test_fetch_nts_writes_every_file_and_reruns_fetch_nothing(sfapi_fake, tmp_path):
    _run(sfapi_fake)
    dest = tmp_path / "pull"
    out = tools.fetch_outputs("T.e470313", str(dest))
    assert (out["n_files"], out["n_fetched"], out["n_present"]) == (3, 3, 0)
    assert [f["status"] for f in out["files"]] == ["fetched"] * 3 and out["dest"] == str(dest)
    for f in out["files"]:
        assert (dest / f["name"]).read_bytes() == sfapi_fake.files[f["path"]]
    assert not list(dest.glob(".*.part"))
    n = len(sfapi_fake.calls)
    again = tools.fetch_outputs("T.e470313", str(dest))
    assert (again["n_fetched"], again["n_present"]) == (0, 3)
    assert sum(1 for c in sfapi_fake.calls[n:] if "/download/" in c[1]) == 0


def test_fetch_refetches_a_file_of_the_wrong_size(sfapi_fake, tmp_path):
    _run(sfapi_fake)
    dest = tmp_path / "pull"
    tools.fetch_outputs("T.e470313", str(dest))
    (dest / "nts.u.T.e470313.00000001.root").write_bytes(b"short")
    out = tools.fetch_outputs("T.e470313", str(dest))
    assert (out["n_fetched"], out["n_present"]) == (1, 2)
    assert (dest / "nts.u.T.e470313.00000001.root").read_bytes() == bytes([1]) * 101


def test_fetch_refuses_the_whole_run_when_one_file_exceeds_the_cap(sfapi_fake, tmp_path, monkeypatch):
    _run(sfapi_fake)
    monkeypatch.setattr(iri, "DOWNLOAD_MAX", 101)
    dest = tmp_path / "pull"
    with pytest.raises(BeamkitError, match=r"1 of 3 nts files exceed the API's 101-byte download cap .*00000002.root \(102 bytes\).*Globus"):
        tools.fetch_outputs("T.e470313", str(dest))
    assert not dest.exists()


def test_fetch_size_mismatch_leaves_no_file(sfapi_fake, tmp_path):
    _run(sfapi_fake)
    orig = sfapi_fake.request

    def truncating(method, url, **kw):
        r = orig(method, url, **kw)
        if "/download/" in url and r._json.get("file"):
            import base64
            r._json["file"] = base64.b64encode(b"x").decode()
        return r
    sfapi_fake.request = truncating
    dest = tmp_path / "pull"
    with pytest.raises(BeamkitError, match="100 bytes on CFS but 1 bytes came back"):
        tools.fetch_outputs("T.e470313", str(dest))
    assert not any(dest.iterdir())


def test_fetch_beamfiles_takes_only_complete_ones(sfapi_fake, tmp_path):
    _run(sfapi_fake)
    dest = tmp_path / "pull"
    entry = tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    assert tools.fetch_outputs("T.e470313", str(dest), kind="beamfiles")["n_files"] == 0
    sfapi_fake.jobs[entry["slurm_id"]].update(state="COMPLETED", exit_code=0)
    sfapi_fake.files[entry["path"]] = b"#BLTrackFile\n1 2 3\n"
    sfapi_fake.files[entry["sidecar"]] = b'{"sha256": "x", "size": 19, "rows": 1, "rows_in": 3, "dropped": 2, "pot": 30, "n_files": 3, "missing_indices": []}'
    tools.beamline_status("T.e470313")
    out = tools.fetch_outputs("T.e470313", str(dest), kind="beamfiles")
    assert out["n_files"] == 1 and out["files"][0]["label"] == "bm"
    assert (dest / out["files"][0]["name"]).read_bytes() == b"#BLTrackFile\n1 2 3\n"


def test_fetch_kind_validated(sfapi_fake, tmp_path):
    _run(sfapi_fake)
    with pytest.raises(BeamkitError, match="kind must be one of"):
        tools.fetch_outputs("T.e470313", str(tmp_path), kind="logs")


def test_fetch_on_a_fermilab_run_is_refused(beamkit_home, tmp_path, monkeypatch):
    from beamkit import paths, records
    from beamkit.backends import fermilab
    rec = records.RunRecord(run_id="F.abc1234", tag="F", dsconf="abc1234", owner="u", run_as="self", deck={},
                            params={}, events_per_job=1, njobs=1, outloc="scratch", slice_size=1, state="created",
                            site="fermilab", block=fermilab.Block(prodtools={"root": "/pt", "commit": "c" * 40, "dev_dir": None}))
    records.save(rec, paths.runs_dir())
    with pytest.raises(BeamkitError, match="fermilab.*dCache"):
        tools.fetch_outputs("F.abc1234", str(tmp_path))
