"""Publishing a built beam file to SAM through prodtools, and the unwind
when that fails. Kept apart from the build so the unwind is tested against
a fake push without reading a dataset or running the ana interpreter."""
import os

from beamkit import bridge, compose


class PublishError(RuntimeError):
    pass


def check_ready(location) -> None:
    """Everything a publish needs that is knowable before the build.
    Discovering it afterwards costs hours of dCache reads and throws the
    built beam file away."""
    if location not in compose.OUTLOCS:
        raise PublishError(f"location must be one of {compose.OUTLOCS}, got {location!r}")
    try:
        available = bridge.push_file_available()
    except bridge.BridgeError as e:
        raise PublishError(str(e)) from e
    if not available:
        raise PublishError("this prodtools has no push_file tool, so publish=True cannot succeed; "
                           "make_beamfile works with publish=False only until prodtools-write "
                           "gains push_file")


def publish(out_txt, staged, location, parents, run_as, confirm, push=None) -> None:
    """Hard-link the built file to its SAM name and push it.

    On any failure discard only what THIS call created: the link, if this
    call made it, and the built file. An os.link that failed because the
    staged name already existed left someone else's file behind it, and
    that file survives. Nothing new remains, so a retry is not blocked."""
    push = push or bridge.push_file
    linked = False
    try:
        os.link(out_txt, staged)
        linked = True
        push(staged, location, list(parents), run_as, confirm)
    except Exception as e:
        if linked:
            try:
                staged.unlink()
            except OSError:
                pass
        try:
            out_txt.unlink()
        except OSError:
            pass
        raise PublishError(f"beam file discarded: publish failed, so this call left nothing new "
                           f"behind; it can be retried: {e}") from e
