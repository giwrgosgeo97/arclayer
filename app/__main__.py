"""Start the app:  python -m app [model.3mf|model.stl]      (add --browser to open it in your web browser instead)

Opening a file while the app is already running (double-click in Explorer) hands the file to the
running window instead of starting a second copy.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import webbrowser

from . import paths

INSTANCE = os.path.join(paths.DATA, "instance.json")


def _log_to_file():
    """A windowed exe has no console: send output and errors to app.log instead."""
    os.makedirs(paths.DATA, exist_ok=True)
    f = open(paths.LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f


def _hand_to_running(path):
    """If the app is already open, ask it to open `path`. True when it took the file."""
    try:
        with open(INSTANCE, encoding="utf-8") as f:
            port = int(json.load(f)["port"])
        base = f"http://127.0.0.1:{port}/api/"
        with urllib.request.urlopen(base + "ping", timeout=1.5) as r:
            if json.load(r).get("app") != "arclayer":
                return False
        try:                                  # let the running window come to the front
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        req = urllib.request.Request(base + "open-request", data=json.dumps({"path": path}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(json.load(r).get("ok"))
    except Exception:
        return False


def _write_instance(port):
    try:
        os.makedirs(paths.DATA, exist_ok=True)
        with open(INSTANCE, "w", encoding="utf-8") as f:
            json.dump({"port": port, "pid": os.getpid()}, f)
    except OSError:
        pass


def _clear_instance():
    try:
        with open(INSTANCE, encoding="utf-8") as f:
            mine = json.load(f).get("pid") == os.getpid()
        if mine:                                  # (Windows can't delete a file that is still open)
            os.remove(INSTANCE)
    except (OSError, ValueError):
        pass


def _splash():
    """What the window shows until the app is loaded."""
    try:
        with open(os.path.join(paths.RESOURCES, "app", "ui", "logo.svg"), encoding="utf-8") as f:
            logo = f.read()
    except OSError:
        logo = ""
    return f"""<!doctype html><meta charset="utf-8"><title>ArcLayer</title><style>
html,body{{height:100%;margin:0;background:#1d1f24;color:#8f98a6;font:15px 'Segoe UI',sans-serif}}
body{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px}}
svg{{width:104px;height:104px}}
b{{font-size:34px;color:#f0f3f7}} b i{{font-style:normal;color:#00c2a4}}
.bar{{width:220px;height:4px;border-radius:2px;background:#2c3038;overflow:hidden}}
.bar i{{display:block;width:40%;height:100%;border-radius:2px;background:#00c2a4;animation:m 1.1s ease-in-out infinite alternate}}
@keyframes m{{from{{transform:translateX(-60%)}}to{{transform:translateX(210%)}}}}
</style><body>{logo}<b><i>Arc</i>Layer</b><div class="bar"><i></i></div><div>Starting…</div></body>"""


def _start_error(e):
    msg = f"{type(e).__name__}: {e}\n\nDetails are in {paths.LOG}"
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "ArcLayer could not start", 0x10)
    except Exception:
        print(msg)
    return 1


def main():
    moved = False
    try:
        moved = paths.migrate_old_name()      # before anything creates the new data folder
    except Exception as e:                    # the old folder is still there: nothing is lost
        print("copying the NonPlanar Slicer data:", e)
    if paths.FROZEN:
        _log_to_file()
        if moved:
            print("ArcLayer: copied your data from", paths._OLD_DATA)
    ap = argparse.ArgumentParser(prog="app")
    ap.add_argument("file", nargs="?", help="a model or project to open (.3mf, .stl, .obj, .ply)")
    ap.add_argument("--browser", action="store_true", help="open in the default web browser")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--serve-only", action="store_true", help="only run the local server (for development)")
    ap.add_argument("--new-window", action="store_true", help="don't hand the file to a running app")
    a = ap.parse_args()
    if a.file:
        a.file = os.path.abspath(a.file)
        if not a.new_window and _hand_to_running(a.file):
            return 0

    app = {}                                  # filled by start_server: "server" (the module), "url"

    def start_server():                       # loads the slicing libraries: a second or two
        if "url" not in app:
            from . import server
            _, port = server.serve(a.port, open_file=a.file)
            app.update(server=server, url=f"http://127.0.0.1:{port}/")
            print(f"ArcLayer running at {app['url']}")
            _write_instance(port)
        return app["url"]

    try:
        if a.serve_only:
            a.browser = True
        if not a.browser:
            try:
                import webview
                # the window comes first, with a splash; the server starts behind it
                win = webview.create_window("ArcLayer", html=_splash(), width=1560, height=960, min_size=(1100, 700),
                                            background_color="#1d1f24")

                def closing():                # runs on the GUI thread; pywebview ends the process right after
                    ok = app["server"].on_closing() if "server" in app else True
                    if ok:
                        _clear_instance()
                    return ok
                win.events.closing += closing

                def boot():
                    try:
                        url = start_server()
                        app["server"].WINDOW = win
                        win.load_url(url)
                    except BaseException as e:
                        import traceback
                        traceback.print_exc()
                        app["error"] = e
                        win.destroy()
                webview.start(boot)
                if "error" in app:
                    raise SystemExit(_start_error(app["error"]))
                return 0
            except Exception as e:
                if "server" in app:
                    app["server"].WINDOW = None
                print(f"App window unavailable ({e}); opening in the browser instead")

        url = start_server()
        if not a.serve_only:
            webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        _clear_instance()


if __name__ == "__main__":
    sys.exit(main())
