"""Entry point for the packaged app (PyInstaller builds 'ArcLayer.exe' from this)."""
import sys
import traceback


def _fatal(msg):
    """No console in the exe: show start-up errors in a message box (and they're in app.log)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "ArcLayer could not start", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        from app.__main__ import main
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:
        traceback.print_exc()
        from app import paths
        _fatal(f"{type(e).__name__}: {e}\n\nDetails are in {paths.LOG}")
        sys.exit(1)
