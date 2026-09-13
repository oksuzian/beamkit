# NERSC

beamkit runs g4bl on Perlmutter through NERSC's APIs with no Fermilab
service in the loop. This page is the setup and reference; the design
is in `specs/2026-09-11-nersc-backend-design.md`.

## Setup

On a gpvm the records dir is `/exp/mu2e/data/users/$USER/beamkit/`; on a
laptop `~/.beamkit/`. Step 2 is laptop-only.

1. Create a NERSC Superfacility API client in Iris (Superfacility API
   Clients, "+ New Client", red level for job submission, your laptop's
   public IP in the allow list). Save the client id to `~/.sfapi/client_id`
   and the private key (PEM or JWK tab) to `~/.sfapi/priv_key.pem`, then
   `chmod 400 ~/.sfapi/priv_key.pem`.
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

   Optional: `transport` ("iri", or "sfapi" for the legacy Superfacility
   API v1.2 at api.nersc.gov with the same client; `machine` defaults to
   "perlmutter"), `procs_per_node` (128), `shared_qos` ("shared") and
   `shared_max_procs` (64).

   Both transports do the same job. `iri` is the facility-neutral
   interface and the default; `sfapi` is the fallback when the IRI v2
   adapter is down (it was, for more than a day, from 2026-09-12).
   `scripts/iri-ping [BEAMKIT_HOME]` tells them apart: token, whoami,
   both resource listings, a CFS `ls`, and the same token against v1.2,
   with a one-line verdict (client problem, IRI adapter problem, or
   facility problem). Exit 0 when the IRI API is up, 2 when not. A full slice of 128 indices takes a whole
   node in `qos`. A smaller slice, up to `shared_max_procs`, runs in
   `shared_qos` non-exclusive and is charged per core, so a 2-job test
   or the last partial slice of a run does not bill a whole node. The
   beam-file job (one process) goes the same way. Slices between 65 and
   127 exceed the shared cap and take a whole node.

4. Register the MCP server (Claude Code `.mcp.json` or Claude Desktop):

   ```json
   {"mcpServers": {"beamkit": {"command": "beamkit-mcp"}}}
   ```

5. `get_server_info` shows `backends.nersc.available: true`. Then
   `run_beamline(tag="G4blBeam", deck_ref="<sha or tag>", run_as="self",
   site="nersc", njobs=10, events_per_job=1000)`, `beamline_status`, and
   `make_beamfile(run_id, "bm", "self", site="nersc")`.

Outputs stay on CFS under `base_dir/runs/<run_id>/out/`; the beam file
under `beamfiles/`. Nothing is declared to SAM. There is no recovery:
`beamline_status` lists missing indices; a new run replaces a short one.
