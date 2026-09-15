# NERSC

beamkit runs g4bl on Perlmutter through NERSC's APIs with no Fermilab
service in the loop. This page is the setup and reference; the design
is in `specs/2026-09-11-nersc-backend-design.md`.

## Setup

On a gpvm the records dir is `/exp/mu2e/data/users/$USER/beamkit/`; on a
laptop `~/.beamkit/`. Step 2 is laptop-only.

1. Create the API client (see "The Superfacility API client" below) and
   save its id and private key under `~/.sfapi/`.
2. Laptop only: `pip install git+https://github.com/oksuzian/beamkit.git@v0.3.1` (Python 3.10+, git on PATH).
3. Write `nersc.toml` in the records dir:

   ```toml
   api            = "https://api.iri.nersc.gov/api/v2"
   sfapi_dir      = "~/.sfapi"
   account        = "m4599"
   base_dir       = "/global/cfs/cdirs/m4599/Users/<nersc-login>/beamkit"
   qos            = "regular"     # "debug" for a 30-minute test
   owner          = "<nersc-login>"
   ```

   Optional keys: `transport`, `machine`, `procs_per_node`, `shared_qos`,
   `shared_max_procs`, see "Configuration keys" below.

4. Laptop only: register the MCP server (Claude Code `.mcp.json` or Claude Desktop):

   ```json
   {"mcpServers": {"beamkit": {"command": "beamkit-mcp"}}}
   ```

5. `get_server_info` shows `backends.nersc.available: true`. Then
   `run_beamline(tag="G4blBeam", deck_ref="<sha or tag>", run_as="self",
   site="nersc", njobs=10, events_per_job=1000, params={"epsMax": "0.01"})`,
   `beamline_status`, and `make_beamfile(run_id, "bm", "self", site="nersc")`.

Outputs stay on CFS under `base_dir/runs/<run_id>/out/`; the beam file
under `beamfiles/`. Nothing is declared to SAM. There is no recovery:
`beamline_status` lists missing indices; a new run replaces a short one.

`fetch_outputs(run_id, dest)` copies the nts files (or, with
`kind="beamfiles"`, the complete beam files) into a local directory
through the API's download endpoint, which carries at most 5 MB per
file on either transport. A run with one larger file is refused whole:
move those with Globus (the NERSC "Perlmutter" collection) or `scp`
from a Perlmutter login node. Files already in `dest` with the CFS
size are not fetched again, so a rerun completes an interrupted copy.
Only the `sfapi` transport can carry a binary file (`binary=true`,
base64); IRI v2's download task fails on a ROOT file with a
`string_unicode` error (checked 2026-09-14), so `fetch_outputs` on the
`iri` transport refuses and says to switch.

## The Superfacility API client

beamkit authenticates to NERSC with a Superfacility API client: a client
id plus an RSA private key, created in Iris and pinned to the IP of the
host that runs beamkit. Both NERSC APIs (IRI v2 and the legacy v1.2)
accept the same client. No NERSC password is ever stored.

**Create it** in Iris: Profile → Superfacility API Clients → "+ New
Client".

- Client name: anything you will recognise, e.g. `mu2e-beamkit-mu2esrv01`.
- Security level: **red**. Job submission needs red; green and amber
  clients can only read.
- IP range: the public IP of the host that runs beamkit, as a /32, e.g.
  `131.225.240.98/32` for mu2esrv01 (`curl https://api.ipify.org` prints
  it). A red client allows at most two ranges.
- Key: either let Iris generate a pair, or paste your own public key so
  a renewal keeps the key you already have (see below).

**Install it** on that host:

```bash
mkdir -m 700 ~/.sfapi
printf '%s' '<client id shown by Iris>' > ~/.sfapi/client_id
cat > ~/.sfapi/priv_key.pem       # paste "Your Private Key (PEM format)", Enter, Ctrl-D
chmod 400 ~/.sfapi/client_id ~/.sfapi/priv_key.pem
```

A JWK private key works too, as `~/.sfapi/priv_key.jwk`. beamkit refuses
a key file readable by group or others, and never writes the key, the
token or the client id into records or logs. Check with
`scripts/iri-ping`: `token OK` and `whoami OK` mean the client is good.

**Lifetime.** A red client lives 48 hours, then dies; Iris offers no
extension. Expiry looks like this from `iri-ping`:

```
token     FAIL token request to https://oidc.nersc.gov/c2id/token failed: invalid_client ...
```

(500s from the facility endpoints with `token OK` are NOT expiry: that is
the API itself, see "Transports" below.)

**Renew** by creating a new client the same way. To keep the existing
private key, choose "provide your own public key" and paste

```bash
openssl rsa -in ~/.sfapi/priv_key.pem -pubout
```

then only `~/.sfapi/client_id` changes. If Iris generated a new pair,
save the new PEM over `priv_key.pem` as above. Delete the old client in
Iris afterwards.

**30-day client.** NERSC grants red clients a 30-day lifetime after a
security review, requested through the Iris client form. Answers that
match how beamkit actually works: the API is used for `/compute` (job
submit and status), `/utilities` (mkdir, ls, upload, download under the
project's CFS directory, no other command execution) and `/tasks`; the
credential lives in one file on one Fermilab host, mode 400, on a
Kerberos-secured home filesystem; installation is copying one client id
into that file, no service restart; a 48-hour client cannot survive a
one-to-two-week production campaign run unattended.

## Configuration keys

Required: `api`, `sfapi_dir`, `account`, `base_dir` (must be under
`/global/cfs/`, the only tree the job binds into the container), `qos`,
`owner` (your NERSC login, a Mu2e name token). Optional:

| key | default | meaning |
|---|---|---|
| `transport` | `"iri"` | `"iri"`: IRI Facility API v2 at `api`. `"sfapi"`: legacy Superfacility API v1.2, same client. |
| `sfapi_api` | `https://api.nersc.gov/api/v1.2` | base URL for the sfapi transport |
| `machine` | `"perlmutter"` | machine name in sfapi paths |
| `procs_per_node` | 128 | indices per Slurm job, one srun task each |
| `shared_qos` | `"shared"` | queue for a slice smaller than a node |
| `shared_max_procs` | 64 | largest slice that still goes to `shared_qos` |
| `image`, `apptainer` | the fnal-wn-el9 image on cvmfs, cvmfs apptainer | the container each task enters |

A full slice of `procs_per_node` indices takes a whole node in `qos`.
A smaller slice, up to `shared_max_procs`, runs in `shared_qos`
non-exclusive and is charged per core, so a 2-job test or the last
partial slice of a run does not bill a whole node; the one-process
beam-file job goes the same way. Slices between 65 and 127 exceed the
shared cap and take a whole node.

## Transports

Both transports do the same job and are selected by `transport`. `iri`
is the facility-neutral interface and the default; `sfapi` is the
fallback when the IRI v2 adapter is down (it was, for more than a day,
from 2026-09-12; both were verified live). `scripts/iri-ping
[BEAMKIT_HOME]` tells the failure modes apart:

```
token     OK                         # client id, key, IP and expiry are fine
whoami    OK {'username': '105241'}
compute   FAIL GET /compute/resources -> 500 ...
cfs       FAIL POST /filesystem/resources -> 500 ...
legacy    OK api.nersc.gov v1.2 status/perlmutter -> 200
verdict   client and facility fine; the IRI v2 adapter is failing -> consult@nersc.gov
```

`token FAIL ... invalid_client` is the client (expired, wrong IP, wrong
key). Facility lines failing with `legacy OK` is the IRI adapter: switch
`transport` to `sfapi` and tell NERSC. Everything failing is NERSC
itself. Exit code 0 when the IRI API is up, 2 when not.

