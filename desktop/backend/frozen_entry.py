"""PyInstaller entry point for the packaged watch helper (see
tools/packaging/ambit_backend.spec and BackendProcess on the C++ side).

A normal checkout never runs this - there `desktop/backend/server.py` is started directly
(by run-desktop.sh) and it shells out to `python tools/<script>.py` for each watch tool. But
in the frozen download there is no separate `python`: the only executable is this frozen
program. So it wears two hats, chosen by the command line:

  ambit-backend                      -> run the HTTP backend (server.main())
  ambit-backend --tool <path> [args] -> behave like `python <path> [args]`
  ambit-backend --workout-builder    -> run the Interval Workout Builder (tools/workout_gui.py)

server.py's run_tool() uses the second form (via sys.executable + "--tool") so each watch
tool still runs in its own fresh process - the isolation server.py deliberately relies on -
without needing Python installed on the user's machine. The third form is what the app's
"Open Workout Builder" button launches: workout_gui.py is already bundled inside this same
frozen program (it lives in tools/), so the button works in a packaged download with nothing
extra to install. See desktop/src/services/intervalsservice.cpp.
"""

import sys


def _bundled_tools_dir():
    # In the frozen build, tools/ is unpacked under sys._MEIPASS (see ambit_backend.spec);
    # from a plain source run this file's siblings are in ../../tools relative to the repo.
    from pathlib import Path

    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "tools"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.parent / "tools"


def _run_builder(script_name):
    import runpy

    tool = str(_bundled_tools_dir() / script_name)
    tools_dir = str(_bundled_tools_dir())
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    sys.argv = [tool] + sys.argv[2:]  # pass through anything after the --*-builder sentinel
    runpy.run_path(tool, run_name="__main__")


def _run_workout_builder():
    _run_builder("workout_gui.py")


def _run_apps_builder():
    _run_builder("apps_gui.py")


def _run_tool():
    # argv: [exe, "--tool", "/path/to/tool.py", *tool_args]
    import runpy
    from pathlib import Path

    tool = sys.argv[2]
    tools_dir = str(Path(tool).resolve().parent)
    if tools_dir not in sys.path:
        # So the tool's own sibling imports (import ambit_format, build_route, ...) resolve
        # from the bundled tools/ dir as well as from the frozen module table.
        sys.path.insert(0, tools_dir)

    # Present the tool with exactly the argv it would see if run as `python tool.py args`.
    sys.argv = [tool] + sys.argv[3:]
    runpy.run_path(tool, run_name="__main__")


def _install_ca_bundle():
    """Point OpenSSL at certifi's CA store.

    A frozen build ships no CA bundle of its own, so Python's ssl falls back to the path
    its OpenSSL was compiled with on the CI runner - which does not exist on a user's
    machine. Every Python-side HTTPS request then dies with CERTIFICATE_VERIFY_FAILED
    ("unable to get local issuer certificate"): on the macOS v0.2.2 .dmg that killed
    /api/firmware AND /api/firmware/download, i.e. the whole firmware feature, on every
    platform. Qt-side requests were unaffected - Qt carries its own TLS stack - which is
    why maps kept working and this looked like a server outage rather than a packaging bug.

    setdefault, so a user or test can still override it from the environment.
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        import certifi
    except ImportError:
        return  # nothing to point at; ssl keeps its (broken) default rather than crashing
    import os

    where = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", where)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", where)


def main():
    _install_ca_bundle()

    if len(sys.argv) >= 2 and sys.argv[1] == "--workout-builder":
        _run_workout_builder()
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "--apps-builder":
        _run_apps_builder()
        return

    if len(sys.argv) >= 3 and sys.argv[1] == "--tool":
        _run_tool()
        return

    import server
    server.main()


if __name__ == "__main__":
    main()
