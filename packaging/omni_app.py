"""Entry point of the packaged application (PyInstaller): ``Omni.exe [command]``; no command opens the window."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()           # workers of the process pools re-run this executable
    from omni.cli import main
    main(sys.argv[1:])
