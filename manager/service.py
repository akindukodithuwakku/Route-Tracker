"""
Windows Service wrapper for the manager, using pywin32. Runs run_server.py's
uvicorn server under LocalSystem so the dashboard/ingest API is always up --
even if no one is logged into the manager PC -- and restarts automatically
if it crashes (failure-recovery actions are configured by install.ps1;
pywin32 doesn't set those on its own).

Install / manage (from source):
    py service.py install
    py service.py start
    py service.py stop
    py service.py remove
    py service.py debug     # requires install first -- pywin32 reads the registered config
    py service.py console   # runs immediately in the foreground, no install/registration needed
    py service.py gencert [extra-hostname-or-ip ...]
    py service.py uninstall-all   # stop+remove service, drop the firewall rule and Add/Remove Programs entry

When frozen into an exe by PyInstaller, the same commands work against the
exe directly (e.g. `LanUsageMonitorManager.exe install`) -- see the note in
client_agent/service.py for why the main block below branches on argv. The
installer (installer_gui.py) drives everything through this one exe rather
than duplicating install logic, so `<exe> --startup auto install` /
`gencert` / `uninstall-all` are also the installer's/uninstaller's contract.
"""

import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import servicemanager
import win32event
import win32service
import win32serviceutil

import run_server

FIREWALL_RULE_NAME = "LAN Usage Monitor - Manager"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\LanUsageMonitorManager"


class ManagerService(win32serviceutil.ServiceFramework):
    _svc_name_ = "LanUsageMonitorManager"
    _svc_display_name_ = "LAN Usage Monitor - Manager Server"
    _svc_description_ = (
        "Receives internet-usage reports from the client PCs and serves "
        "the localhost monitoring dashboard."
    )

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = threading.Event()
        self._worker_thread = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.stop_event.set()
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self._worker_thread = threading.Thread(
            target=run_server.run_forever, args=(self.stop_event,), daemon=True
        )
        self._worker_thread.start()
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)
        self._worker_thread.join(timeout=15.0)


def _gencert(extra_names):
    import generate_cert
    try:
        path = generate_cert.generate(extra_names=extra_names, force=True)
        print(f"wrote {path}")
    except Exception as e:
        print(f"ERROR generating cert: {e}")
        sys.exit(1)


def _uninstall_all():
    print("Stopping service...")
    subprocess.run(["sc.exe", "stop", ManagerService._svc_name_], capture_output=True)
    time.sleep(2)
    print("Removing service...")
    subprocess.run(["sc.exe", "delete", ManagerService._svc_name_], capture_output=True)
    print("Removing firewall rule...")
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE_NAME}"],
        capture_output=True,
    )
    print("Removing Add/Remove Programs entry...")
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)
    except FileNotFoundError:
        pass
    print("Done. The install folder and its data/config/certs were left in place -- delete it manually if you're done with it.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(ManagerService)
        servicemanager.StartServiceCtrlDispatcher()
    elif len(sys.argv) == 2 and sys.argv[1] == "console":
        run_server.run_forever()
    elif len(sys.argv) >= 2 and sys.argv[1] == "gencert":
        _gencert(sys.argv[2:])
    elif len(sys.argv) == 2 and sys.argv[1] == "uninstall-all":
        _uninstall_all()
    else:
        win32serviceutil.HandleCommandLine(ManagerService)
