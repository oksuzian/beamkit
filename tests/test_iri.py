import io
from pathlib import Path

import pytest

from beamkit import iri
from beamkit.nersc_config import NerscConfig
from tests.fake_iri import CFS, COMPUTE, FakeSession


@pytest.fixture
def cfg(tmp_path):
    return NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=tmp_path, account="m4599",
                       base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="regular", owner="u",
                       procs_per_node=128, image="/cvmfs/img", apptainer="/cvmfs/apptainer")


@pytest.fixture
def client(cfg):
    s = FakeSession()
    c = iri.IriClient(cfg, token_provider=lambda: "tok", session=s)
    c.fake = s
    return c


def test_bearer_header_on_every_call(client):
    client.whoami()
    method, url, kw = client.fake.calls[-1]
    assert kw["headers"]["Authorization"] == "Bearer tok" and url.endswith("/account/whoami")


def test_resource_ids_by_name(client):
    assert client.resource_id("compute", "compute") == COMPUTE
    assert client.resource_id("filesystem", "cfs") == CFS
    assert client.fake.calls[-1][0] == "POST"      # filesystem listing is POST


def test_unknown_resource_name_lists_what_exists(client):
    with pytest.raises(iri.IriError, match="jobs, compute"):
        client.resource_id("compute", "gpu")


def test_mkdir_ls_upload_download_round_trip(client, tmp_path):
    # The API's mkdir is not -p: build the parents in order first, same as
    # a real caller (or _remote_layout) must.
    client.mkdir("/global/cfs/cdirs/m4599/Users/u/beamkit")
    client.mkdir("/global/cfs/cdirs/m4599/Users/u/beamkit/runs")
    d = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"
    client.mkdir(d)
    assert client.exists(d) and not client.exists(d + "/nope")
    local = tmp_path / "job.sh"
    local.write_text("#!/bin/bash\necho hi\n")
    client.upload(local, d + "/job.sh")
    names = [e["name"] for e in client.ls(d)]
    assert names == [d + "/job.sh"]
    assert client.download(d + "/job.sh") == "#!/bin/bash\necho hi\n"


def test_mkdir_without_parent_surfaces_the_api_error(client):
    with pytest.raises(iri.IriError, match="No such file or directory"):
        client.mkdir("/global/cfs/cdirs/m4599/Users/u/absent/child")


def test_upload_over_cap_is_refused_before_any_request(client, tmp_path):
    big = tmp_path / "big.tar"
    big.write_bytes(b"x" * (iri.UPLOAD_MAX + 1))
    n = len(client.fake.calls)
    with pytest.raises(iri.IriError, match="5242881 bytes exceeds the 5242880-byte upload cap"):
        client.upload(big, "/global/cfs/x/big.tar")
    assert len(client.fake.calls) == n


def test_upload_into_missing_dir_surfaces_the_task_error(client, tmp_path):
    local = tmp_path / "a"
    local.write_text("a")
    with pytest.raises(iri.IriError, match="No such file"):
        client.upload(local, "/global/cfs/absent/a")


def test_submit_and_status(client):
    jid = client.submit({"name": "x"})
    assert jid == "58197742"
    assert client.status(jid)["state"] == "queued"
    client.fake.jobs[jid]["state"] = "completed"
    st = client.status(jid)
    assert st["state"] == "completed" and st["meta_data"]["nodelist"] == "nid004381"


def test_http_error_carries_status_and_detail(client):
    client.fake.fail_submit_at = 0
    with pytest.raises(iri.IriError) as e:
        client.submit({"name": "x"})
    assert e.value.status == 500 and "sbatch: error" in e.value.detail


def test_401_names_ip_pinning_and_client_lifetime(client, monkeypatch):
    def unauthorized(method, url, **kw):
        from tests.fake_iri import FakeResponse
        return FakeResponse(401, {"detail": "Facility Specific authentication failed: 403: Invalid token"})
    monkeypatch.setattr(client.fake, "request", unauthorized)
    with pytest.raises(iri.IriError, match="source IP.*48 h"):
        client.whoami()


def test_wait_task_times_out_with_the_task_id(client, monkeypatch):
    tid = "t-1"
    client.fake.tasks[tid] = None
    def running(method, url, **kw):
        from tests.fake_iri import FakeResponse
        return FakeResponse(200, {"status": "running", "result": None})
    monkeypatch.setattr(client.fake, "request", running)
    monkeypatch.setattr(iri.time, "sleep", lambda s: None)
    with pytest.raises(iri.IriError, match="task t-1 still running after 0 s"):
        client.wait_task({"task_id": tid, "task_uri": f"{client.cfg.api}/task/{tid}"}, timeout_s=0)


def test_sfapi_token_refuses_a_public_jwk(cfg, tmp_path):
    (tmp_path / "client_id").write_text("abcdefghijklm")
    jwk = tmp_path / "priv_key.jwk"
    jwk.write_text('{"kty": "RSA", "n": "x", "e": "AQAB"}')
    jwk.chmod(0o400)
    with pytest.raises(iri.IriError, match="PUBLIC JWK"):
        iri.sfapi_token(cfg)
