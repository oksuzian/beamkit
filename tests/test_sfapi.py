import pytest

from beamkit import BeamkitError, iri, nersc_config, sfapi
from beamkit.backends import nersc
from beamkit.nersc_config import NerscConfig
from tests.fake_sfapi import FakeSfapiSession

RUN_DIR = "/global/cfs/cdirs/m4599/Users/u/beamkit/runs/T.e470313"


@pytest.fixture
def cfg(tmp_path):
    return NerscConfig(api="https://api.iri.nersc.gov/api/v2", sfapi_dir=tmp_path, account="m4599",
                       base_dir="/global/cfs/cdirs/m4599/Users/u/beamkit", qos="debug", owner="u",
                       procs_per_node=128, image="/cvmfs/img", apptainer="/cvmfs/apptainer", transport="sfapi")


@pytest.fixture
def client(cfg):
    s = FakeSfapiSession()
    c = sfapi.SfapiClient(cfg, token_provider=lambda: "tok", session=s)
    c.fake = s
    return c


def test_render_sbatch_from_job_spec(cfg):
    spec = nersc.job_spec(cfg, run_id="T.e470313", run_dir=RUN_DIR, offset=128, count=128, duration=2900)
    script = sfapi.render_sbatch(spec)
    assert script.startswith("#!/bin/bash\n#SBATCH -J beamkit.T.e470313.128\n")
    for line in ("#SBATCH -A m4599", "#SBATCH -q debug", "#SBATCH -N 1", "#SBATCH -n 128",
                 "#SBATCH --ntasks-per-node=128", "#SBATCH -c 1", "#SBATCH -t 49", "#SBATCH --exclusive",
                 "#SBATCH -C cpu", "#SBATCH --export=NONE",
                 f"#SBATCH -o {RUN_DIR}/slurm/128.out", f"#SBATCH -e {RUN_DIR}/slurm/128.err",
                 f"cd {RUN_DIR} || exit 2", "export BK_OFFSET=128", f"exec srun --export=ALL /bin/bash {RUN_DIR}/job.sh"):
        assert line in script, line


def test_render_sbatch_shared_slice_is_not_exclusive(cfg):
    spec = nersc.job_spec(cfg, run_id="T.e470313", run_dir=RUN_DIR, offset=0, count=2, duration=60)
    script = sfapi.render_sbatch(spec)
    assert "#SBATCH -q shared" in script and "--exclusive" not in script and "#SBATCH -t 1\n" in script
    assert "-L " not in script and "module load" not in script


@pytest.mark.parametrize("slurm,want", [
    ("PENDING", "queued"), ("RUNNING", "active"), ("COMPLETING", "active"), ("COMPLETED", "completed"),
    ("FAILED", "failed"), ("TIMEOUT", "failed"), ("OUT_OF_MEMORY", "failed"),
    ("CANCELLED", "canceled"), ("CANCELLED by 105241", "canceled"),
])
def test_map_state(slurm, want):
    assert sfapi.map_state(slurm) == want


def test_map_state_refuses_unknown():
    with pytest.raises(iri.IriError, match="unknown Slurm state"):
        sfapi.map_state("WEIRD")


def test_ls_returns_absolute_names_without_dot_entries(client):
    client.fake.dirs |= {RUN_DIR, RUN_DIR + "/out"}
    client.fake.files[RUN_DIR + "/out/a.root"] = b"xx"
    got = client.ls(RUN_DIR + "/out")
    assert got == [{"name": RUN_DIR + "/out/a.root", "type": "f", "size": "2", "user": "u", "group": "m4599",
                    "permissions": "-rw-r--r--", "last_modified": "2026-09-12T14:28:17"}]
    assert {e["name"] for e in client.ls(RUN_DIR)} == {RUN_DIR + "/out"}


def test_error_inside_200_is_an_irierror_and_exists_reads_it(client):
    with pytest.raises(iri.IriError, match="No such file"):
        client.ls(RUN_DIR + "/nope")
    assert client.exists(RUN_DIR + "/nope") is False
    client.fake.dirs.add(RUN_DIR)
    assert client.exists(RUN_DIR) is True


def test_mkdir_creates_parents_through_one_command(client):
    client.mkdir(RUN_DIR + "/out")
    assert RUN_DIR + "/out" in client.fake.dirs
    cmds = [c for c in client.fake.calls if c[1].endswith("/utilities/command/perlmutter")]
    assert len(cmds) == 1 and cmds[0][2]["data"]["executable"] == f"mkdir -p {RUN_DIR}/out"


def test_upload_download_round_trip_and_cap(client, tmp_path, monkeypatch):
    client.fake.dirs.add(RUN_DIR)
    f = tmp_path / "job.sh"
    f.write_text("echo hi\n")
    client.upload(f, RUN_DIR + "/job.sh")
    assert client.download(RUN_DIR + "/job.sh") == "echo hi\n"
    assert [c[0] for c in client.fake.calls if "/utilities/upload/" in c[1]] == ["PUT"]
    monkeypatch.setattr(iri, "UPLOAD_MAX", 3)
    with pytest.raises(iri.IriError, match="upload cap"):
        client.upload(f, RUN_DIR + "/job2.sh")
    assert not any("/job2.sh" in c[1] for c in client.fake.calls)
    with pytest.raises(iri.IriError, match="No such file"):
        client.download(RUN_DIR + "/missing")


def test_submit_and_status_round_trip(client, cfg):
    spec = nersc.job_spec(cfg, run_id="T.e470313", run_dir=RUN_DIR, offset=0, count=2, duration=60)
    jid = client.submit(spec)
    assert jid == "58197742"
    stored = client.fake.jobs[jid]["spec"]
    assert stored["environment"] == {"BK_OFFSET": "0"} and stored["attributes"]["queue_name"] == "shared"
    st = client.status(jid)
    assert st["state"] == "queued" and st["exit_code"] == 0
    client.fake.jobs[jid].update(state="COMPLETED", exit_code=0)
    st = client.status(jid)
    assert st["state"] == "completed" and st["meta_data"]["nodelist"] == "nid004381"
    client.fake.jobs[jid].update(state="FAILED", exit_code=99)
    assert client.status(jid)["exit_code"] == 99
    # not yet in sacct: queued, never an error
    assert client.status("1")["state"] == "queued"
    assert all(c[2]["params"]["cached"] == "false" for c in client.fake.calls
               if c[0] == "GET" and c[1].endswith("/compute/jobs/perlmutter"))


def test_failed_submit_is_an_irierror(client, cfg):
    client.fake.fail_submit_at = 0
    spec = nersc.job_spec(cfg, run_id="T.e470313", run_dir=RUN_DIR, offset=0, count=2, duration=60)
    with pytest.raises(iri.IriError, match="Batch job submission failed"):
        client.submit(spec)


def test_make_client_picks_transport(cfg):
    from dataclasses import replace
    assert isinstance(nersc.make_client(cfg), sfapi.SfapiClient)
    assert isinstance(nersc.make_client(replace(cfg, transport="iri")), iri.IriClient)


# --- the whole NERSC backend on the sfapi transport

SHA = "e470313" + "0" * 33
BASE = "/global/cfs/cdirs/m4599/Users/u/beamkit"
RD = f"{BASE}/runs/T.e470313"


@pytest.fixture
def sfapi_fake(monkeypatch, beamkit_home, tmp_path):
    from beamkit import decks
    deck = tmp_path / "deckcache" / SHA[:12]
    deck.mkdir(parents=True)
    (deck / "Mu2E.in").write_text("param -unset First_Event=1\n")
    sf = tmp_path / "sfapi"
    sf.mkdir()
    (sf / "client_id").write_text("abcdefghijklm")
    key = sf / "priv_key.pem"
    key.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    key.chmod(0o400)
    beamkit_home.mkdir(parents=True)
    (beamkit_home / "nersc.toml").write_text(
        f'api = "https://api.iri.nersc.gov/api/v2"\nsfapi_dir = "{sf}"\naccount = "m4599"\n'
        f'base_dir = "{BASE}"\nqos = "debug"\nowner = "u"\nprocs_per_node = 128\ntransport = "sfapi"\n')
    s = FakeSfapiSession()
    monkeypatch.setattr(nersc, "make_client", lambda cfg: sfapi.SfapiClient(cfg, token_provider=lambda: "tok", session=s))
    monkeypatch.setattr(decks, "materialize", lambda url, ref, cache: decks.DeckPin(url, ref, SHA, str(deck), True))
    return s


def test_backend_end_to_end_on_sfapi(sfapi_fake):
    from beamkit import tools
    fake = sfapi_fake
    rec = tools.run_beamline(tag="T", run_as="self", deck_ref=SHA, site="nersc", njobs=300, events_per_job=10,
                             params={"epsMax": "0.01"})
    assert rec["state"] == "submitted" and rec["nersc"]["config"]["transport"] == "sfapi"
    jobs = rec["nersc"]["jobs"]
    assert [(j["offset"], j["count"]) for j in jobs] == [(0, 128), (128, 128), (256, 44)]
    assert {RD, RD + "/out", RD + "/slurm", RD + "/beamfiles"} <= fake.dirs
    assert set(fake.files) == {RD + "/cnf.u.T.e470313.0.tar", RD + "/job.sh", RD + "/inner.sh"}
    specs = [fake.jobs[j["slurm_id"]]["spec"] for j in jobs]
    assert [s["attributes"]["queue_name"] for s in specs] == ["debug", "debug", "shared"]
    assert [s["resources"]["exclusive_node_use"] for s in specs] == [True, True, False]
    assert specs[1]["environment"] == {"BK_OFFSET": "128"}
    assert "epsMax='0.01'" in fake.files[RD + "/inner.sh"].decode() or "epsMax=0.01" in fake.files[RD + "/inner.sh"].decode()

    st = tools.beamline_status("T.e470313")
    assert [j["state"] for j in st["nersc"]["jobs"]] == ["queued"] * 3 and st["record"]["state"] == "submitted"

    for j in jobs:
        fake.jobs[j["slurm_id"]].update(state="COMPLETED", exit_code=0)
    for idx in range(300):
        seq = f"{idx:08d}"
        fake.files[f"{RD}/out/log.u.T.e470313.{seq}.log"] = b"BK_DONE\n"
        if idx != 7:
            fake.files[f"{RD}/out/nts.u.T.e470313.{seq}.root"] = b"r" * (100 + idx)
    st = tools.beamline_status("T.e470313")
    assert st["record"]["state"] == "short" and st["nersc"]["outputs"]["missing"] == [7]
    assert st["nersc"]["jobs"][0]["node"] == "nid004381"
    fake.files[f"{RD}/out/nts.u.T.e470313.{7:08d}.root"] = b"r"
    assert tools.beamline_status("T.e470313")["record"]["state"] == "complete"
    out = tools.beamline_outputs("T.e470313")
    assert len(out["files"]) == 300 and out["files"][0]["path"] == f"{RD}/out/nts.u.T.e470313.00000000.root"

    entry = tools.make_beamfile("T.e470313", "bm", "self", site="nersc")
    bspec = fake.jobs[entry["slurm_id"]]["spec"]
    assert bspec["attributes"]["queue_name"] == "shared" and bspec["resources"]["process_count"] == 1
    assert f"{RD}/beamfiles/beamfile.bm.sh" in fake.files and f"{RD}/beamfiles/beamfile_job.bm.py" in fake.files
    assert bspec["script"].endswith(f"exec srun --export=ALL /bin/bash {RD}/beamfiles/beamfile.bm.sh\n")


def test_backend_submit_failure_on_sfapi_is_retryable(sfapi_fake):
    from beamkit import tools
    sfapi_fake.fail_submit_at = 1
    with pytest.raises(BeamkitError, match="Batch job submission failed"):
        tools.run_beamline(tag="T", run_as="self", deck_ref=SHA, site="nersc", njobs=300, events_per_job=10)
    sfapi_fake.fail_submit_at = None
    rec = tools.submit_run("T.e470313", "self")
    assert rec["state"] == "submitted" and len(rec["nersc"]["jobs"]) == 3
