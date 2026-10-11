"""Launch desktop applications without hijacking the user's window.

Why this exists
---------------
On Windows, starting a GUI program normally brings it to the foreground. If
something else is driving the machine - a script, an assistant, a scheduled
task - that means the window jumps in front of whatever the user is typing
into. Their keystrokes go somewhere they did not intend.

`subprocess.Popen` gives no hint that this is a problem, and the obvious fixes
do not all work. What follows was measured rather than assumed:

===========================  ==========================================
Approach                     Result
===========================  ==========================================
default `Popen`              steals focus - Notepad and Chrome both tested
`SW_SHOWNOACTIVATE` (4)      works for Notepad, **unreliable for Chrome**
`SW_SHOWMINNOACTIVE` (7)     **works for both** - this is the default here
off-screen position          does not steal focus
`SW_HIDE`                    does not steal focus
===========================  ==========================================

Two caveats worth knowing before choosing:

- Off-screen and hidden windows are not free. Chromium **does not build an
  accessibility tree** for an invisible window, so any automation that reads
  the page through UIA stops working. Hiding a window to be polite can quietly
  break the thing you were launching it for.
- `os.startfile` (the usual way to launch a registered application) has no
  equivalent switch. That path will steal focus, so the module provides a
  minimise-as-fallback for it.

The three layers
----------------
For a long-running tool that may launch the same program repeatedly:

1. **Probe first** - if it is already running, reuse it. Restarting is itself a
   source of interruption: one application on the test machine was found to
   have twenty-odd processes running because nothing checked.
2. **Launch quietly** - `quiet_startupinfo()` on the spawn call.
3. **Minimise as a fallback** - if focus was taken anyway, put it back. A few
   seconds late is much better than not at all.

Everything degrades gracefully off Windows: the helpers return `None`, `False`
or `0` rather than raising, so callers do not need platform branches.

Usage
-----
    from qakit.quiet_launch import launch_or_reuse

    ok, detail = launch_or_reuse(r"C:\\Windows\\System32\\notepad.exe")

    # or from the command line
    python -m qakit.quiet_launch --exe notepad.exe
    python -m qakit.quiet_launch --running notepad.exe
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

#: Window show state: minimised and not activated.
SW_SHOWMINNOACTIVE = 7

#: Window show state used by the fallback minimiser.
SW_MINIMIZE = 6


@dataclass(frozen=True)
class AppSpec:
    """One launchable application and how to recognise it."""

    name: str
    aliases: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()   # absolute paths, tried in order
    path_names: tuple[str, ...] = ()    # looked up on PATH
    startfile: str = ""                 # handed to os.startfile (no quiet switch)
    processes: tuple[str, ...] = ()     # image names, used to detect "already running"
    window_titles: tuple[str, ...] = () # window titles, used by the minimiser
    extra: dict = field(default_factory=dict)


#: A small default registry. Extend it in your own project rather than here.
DEFAULT_APPS: tuple[AppSpec, ...] = (
    AppSpec("Notepad", ("notepad", "text editor"), startfile="notepad.exe",
            path_names=("notepad.exe",), processes=("notepad.exe",),
            window_titles=("Notepad", "Untitled - Notepad")),
    AppSpec("Calculator", ("calculator", "calc"), startfile="calc.exe",
            path_names=("calc.exe",), processes=("calculator.exe", "calc.exe"),
            window_titles=("Calculator",)),
    AppSpec("File Explorer", ("explorer", "files"), startfile="explorer.exe",
            path_names=("explorer.exe",), processes=("explorer.exe",),
            window_titles=("File Explorer",)),
    AppSpec("Command Prompt", ("cmd", "terminal"), startfile="cmd.exe",
            path_names=("cmd.exe",), processes=("cmd.exe",),
            window_titles=("Command Prompt",)),
    AppSpec("PowerShell", ("powershell", "pwsh"), startfile="powershell.exe",
            path_names=("powershell.exe", "pwsh.exe"),
            processes=("powershell.exe", "pwsh.exe"), window_titles=("PowerShell",)),
    AppSpec("VS Code", ("vscode", "code"), path_names=("code.cmd", "code.exe"),
            processes=("code.exe",), window_titles=("Visual Studio Code",)),
)


def quiet_startupinfo():
    """Return a STARTUPINFO that shows the window without activating it.

    Returns `None` when the platform has no STARTUPINFO (or it cannot be
    built). Callers should pass the result straight through to `Popen`, which
    accepts `None` and falls back to default behaviour - launching the program
    matters more than the focus detail, so this must never be fatal.
    """
    try:
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = SW_SHOWMINNOACTIVE
        return info
    except Exception:
        # Not Windows, or the attribute is missing. Default behaviour is the
        # correct fallback, and refusing to launch would be worse.
        return None


def find_executable(spec: AppSpec) -> str:
    """Resolve an AppSpec to a concrete path, or an empty string."""
    for path in spec.executables:
        if path and os.path.isfile(path):
            return path
    for name in spec.path_names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def launch(exe: str, args: tuple[str, ...] = ()) -> tuple[bool, str]:
    """Start a program without stealing focus.

    Returns `(succeeded, detail)`. On failure the detail says why - a launcher
    that returns nothing on failure forces the caller to guess, and the user
    ends up thinking the tool ignored them.
    """
    if not exe:
        return False, "no executable given"

    resolved = shutil.which(exe) or exe
    if not os.path.isfile(resolved):
        return False, "not found: %s" % exe

    try:
        subprocess.Popen([resolved, *args], startupinfo=quiet_startupinfo())  # noqa: S603
        return True, resolved
    except Exception as exc:
        return False, "launch failed (%s): %s" % (type(exc).__name__, str(exc)[:120])


def launch_registered(target: str) -> tuple[bool, str]:
    """Launch through `os.startfile` (registered apps, protocols, documents).

    There is no quiet switch on this path, so expect it to take focus. Follow
    it with `minimize_windows()` if that matters.
    """
    try:
        os.startfile(target)  # noqa: S606 - Windows-only, and that is the point
        return True, target
    except Exception as exc:
        return False, "not a registered target: %s (%s)" % (target, type(exc).__name__)


def running_processes() -> set[str]:
    """Return the set of running image names, lower-cased. Empty if unavailable."""
    try:
        done = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except Exception:
        # Cannot enumerate processes. Return empty rather than raising: the
        # caller then launches, which is the same as the old behaviour.
        return set()
    names: set[str] = set()
    for line in (done.stdout or "").splitlines():
        first = line.split(",", 1)[0].strip().strip('"').lower()
        if first:
            names.add(first)
    return names


def is_running(spec: AppSpec) -> bool:
    """True if the application appears to be running already.

    Read-only: it never starts or stops anything. When the answer is unknown it
    returns `False`, so the worst case is one extra launch rather than a tool
    that silently refuses to start anything.
    """
    wanted = {name.lower() for name in spec.processes}
    if not wanted:
        return False
    return bool(wanted & running_processes())


def minimize_windows(titles: tuple[str, ...] | list[str]) -> int:
    """Minimise the top-level windows with these titles. Returns how many.

    Uses only `FindWindowW` / `ShowWindow`. It does not resize, move, kill or
    send keystrokes, and it touches no mouse state.

    The `argtypes` / `restype` declarations are not optional: without them the
    window handle is truncated to 32 bits on 64-bit Python and `FindWindowW`
    returns something that cannot be used.
    """
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.c_bool
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_bool
    except Exception:
        # Not Windows, or the API is unavailable. Report zero rather than
        # pretending something was minimised.
        return 0

    count = 0
    for title in titles or ():
        try:
            handle = user32.FindWindowW(None, str(title))
        except Exception:
            # One bad title must not abort the rest.
            continue
        if not handle:
            continue
        try:
            if user32.IsWindowVisible(handle):
                user32.ShowWindow(handle, SW_MINIMIZE)
                count += 1
        except Exception:
            # Could not minimise this one; keep going and report the true count.
            continue
    return count


def launch_or_reuse(spec: AppSpec) -> tuple[bool, str]:
    """Apply the three layers: probe, launch quietly, minimise as a fallback.

    Returns `(succeeded, detail)`. When the application is already running the
    detail says so and nothing is started - which is the point.
    """
    if is_running(spec):
        return True, "already running: %s" % spec.name

    exe = find_executable(spec)
    if exe:
        ok, detail = launch(exe)
        if not ok:
            return False, detail
        # Layer 3: some builds ignore the startup flag.
        minimized = minimize_windows(spec.window_titles)
        if minimized:
            return True, "%s (focus reclaimed from %d window(s))" % (detail, minimized)
        return True, detail

    if spec.startfile:
        ok, detail = launch_registered(spec.startfile)
        if not ok:
            return False, detail
        minimized = minimize_windows(spec.window_titles)
        return True, "%s (startfile, %d window(s) minimised)" % (detail, minimized)

    return False, "no way to launch %s: no executable and no registered target" % spec.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch applications without stealing focus.")
    parser.add_argument("--exe", help="executable or command to launch")
    parser.add_argument("--args", nargs="*", default=[], help="arguments for --exe")
    parser.add_argument("--running", help="check whether this image name is running, then exit")
    parser.add_argument("--minimize", action="append", default=None,
                        help="minimise windows with this title (repeatable)")
    parser.add_argument("--list-apps", action="store_true", help="print the default registry")
    args = parser.parse_args(argv)

    if args.list_apps:
        for spec in DEFAULT_APPS:
            exe = find_executable(spec) or "(not found)"
            print("  %-16s %-28s running=%s" % (spec.name, exe, is_running(spec)))
        return 0

    if args.running:
        spec = AppSpec("probe", processes=(args.running,))
        found = is_running(spec)
        print("  %s is %s" % (args.running, "running" if found else "not running"))
        return 0 if found else 1

    if args.minimize:
        count = minimize_windows(tuple(args.minimize))
        print("  minimised %d window(s)" % count)
        return 0 if count else 1

    if args.exe:
        ok, detail = launch(args.exe, tuple(args.args))
        print("  %s  %s" % ("OK" if ok else "FAILED", detail))
        return 0 if ok else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
