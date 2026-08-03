"""
Windows Service wrapper for the client agent, using pywin32. This is what
makes the agent "always running": it starts automatically on boot (before
any user logs in, since it runs as LocalSystem), and Windows' Service
Control Manager restarts it automatically if the process ever dies (see
install.ps1, which configures the failure-recovery actions -- pywin32 does
not set those on its own).

Install / manage (from source):
    py service.py install
    py service.py start
    py service.py stop
    py service.py remove
    py service.py debug     # requires install first -- pywin32 reads the registered config
    py service.py console   # runs immediately in the foreground, no install/registration needed
    py service.py uninstall-all   # stop+remove service and drop the Add/Remove Programs entry

When frozen into an exe by PyInstaller, the same commands work against the
exe directly (e.g. `LanUsageMonitorAgent.exe install`), and PyInstaller sets
sys.frozen -- pywin32 detects that and points the service registration at
this exe itself rather than the normal pythonservice.exe host, which is why
SvcDoRun below has to work whether launched by SCM (no argv) or by hand.
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

import agent_main

UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\LanUsageMonitorAgent"


class AgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "LanUsageMonitorAgent"
    _svc_display_name_ = "LAN Usage Monitor - Client Agent"
    _svc_description_ = (
        "Captures this PC's internet usage (sites visited, time spent, "
        "bandwidth) and reports it to the manager PC's dashboard."
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
            target=agent_main.run_forever, args=(self.stop_event,), daemon=True
        )
        self._worker_thread.start()
        # Block here until SvcStop signals hWaitStop; SvcDoRun returning is
        # what tells SCM the service has stopped.
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)
        self._worker_thread.join(timeout=15.0)


def _uninstall_all():
    print("Stopping service...")
    subprocess.run(["sc.exe", "stop", AgentService._svc_name_], capture_output=True)
    time.sleep(2)
    print("Removing service...")
    subprocess.run(["sc.exe", "delete", AgentService._svc_name_], capture_output=True)
    print("Removing Add/Remove Programs entry...")
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY)
    except FileNotFoundError:
        pass
    print("Done. The install folder and its config/logs were left in place -- delete it manually if you're done with it.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # No args: this is how the Service Control Manager launches a
        # frozen/self-hosting service -- host it directly instead of going
        # through pythonservice.exe.
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(AgentService)
        servicemanager.StartServiceCtrlDispatcher()
    elif len(sys.argv) == 2 and sys.argv[1] == "console":
        agent_main.run_forever()
    elif len(sys.argv) == 2 and sys.argv[1] == "uninstall-all":
        _uninstall_all()
    else:
        win32serviceutil.HandleCommandLine(AgentService)
