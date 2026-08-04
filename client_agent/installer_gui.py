"""
"Route Tracker - Agent Setup" -- a self-contained installer GUI.

Built by PyInstaller into a single ClientAgentSetup.exe that embeds the
whole agent payload (LanUsageMonitorAgent.exe + its files, including the
bundled WinDivert driver) as data. When the user double-clicks it, this
script:
  1. Relaunches itself elevated (UAC prompt) if not already admin
  2. Asks for exactly two values -- the cloud endpoint URL and the shared
     enrollment token, both printed together by scripts/setup-project.js and
     identical on every PC. There is nothing else to configure: the agent
     self-enrolls using this PC's own hostname on first run (see
     enrollment.py) and gets its own device_id/device_key from the cloud,
     so no per-PC id/key pair needs to be typed in here.
  3. Copies the payload into place and writes config.json
  4. Registers + starts the Windows Service and configures auto-restart

Every privileged step is delegated to LanUsageMonitorAgent.exe itself
(install / uninstall-all) rather than duplicated here.
"""

import ctypes
import json
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

APP_NAME = "Route Tracker - Agent"
SERVICE_NAME = "LanUsageMonitorAgent"
DEFAULT_INSTALL_DIR = r"C:\Program Files\Route Tracker\Agent"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\LanUsageMonitorAgent"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated():
    params = " ".join(f'"{a}"' for a in sys.argv[1:])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)


def payload_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    candidate = base / "payload"
    if candidate.exists():
        return candidate
    return Path(__file__).parent / "dist" / "LanUsageMonitorAgent"


class InstallerApp:
    def __init__(self, root):
        self.root = root
        root.title(f"{APP_NAME} Setup")
        root.geometry("560x460")
        root.resizable(False, False)

        tk.Label(root, text=APP_NAME, font=("Segoe UI", 14, "bold")).pack(pady=(16, 4))
        tk.Label(
            root,
            text="Installs the agent that reports this PC's internet usage\n"
                 "to your Route Tracker dashboard. Paste the two values shown\n"
                 "by the setup script -- they're the same on every PC.",
            justify="center",
        ).pack(pady=(0, 14))

        form = tk.Frame(root)
        form.pack(fill="x", padx=24)

        tk.Label(form, text="Cloud endpoint URL:").grid(row=0, column=0, sticky="w", pady=4)
        self.cloud_url = tk.StringVar(value="")
        tk.Entry(form, textvariable=self.cloud_url, width=46).grid(row=0, column=1, sticky="w")

        tk.Label(form, text="Enrollment token:").grid(row=1, column=0, sticky="w", pady=4)
        self.token = tk.StringVar(value="")
        tk.Entry(form, textvariable=self.token, width=46).grid(row=1, column=1, sticky="w")

        tk.Label(form, text="Install to:").grid(row=2, column=0, sticky="w", pady=4)
        self.install_dir = tk.StringVar(value=DEFAULT_INSTALL_DIR)
        tk.Entry(form, textvariable=self.install_dir, width=38).grid(row=2, column=1, sticky="w")
        tk.Button(form, text="Browse...", command=self._browse_dir).grid(row=2, column=2, padx=6)

        tk.Label(
            root,
            text="This PC will appear on the dashboard by itself within a few\n"
                 "minutes of installing -- nothing to configure on the dashboard side.",
            fg="#666", font=("Segoe UI", 8), justify="center",
        ).pack(pady=(10, 0))

        self.install_btn = tk.Button(root, text="Install", width=16, command=self._start_install)
        self.install_btn.pack(pady=12)

        self.log = scrolledtext.ScrolledText(root, height=11, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=24, pady=(0, 16))

    def _browse_dir(self):
        chosen = filedialog.askdirectory()
        if chosen:
            self.install_dir.set(str(Path(chosen) / "Route Tracker" / "Agent"))

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.update_idletasks()

    def _run(self, args):
        self._log("> " + " ".join(args))
        result = subprocess.run(args, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.stdout.strip():
            self._log(result.stdout.strip())
        if result.stderr.strip():
            self._log(result.stderr.strip())
        return result

    def _start_install(self):
        cloud_url = self.cloud_url.get().strip().rstrip("/")
        token = self.token.get().strip()
        if not cloud_url.startswith("https://") and not cloud_url.startswith("http://"):
            messagebox.showerror(APP_NAME, "Cloud endpoint URL must start with https://")
            return
        if not token:
            messagebox.showerror(APP_NAME, "Paste the enrollment token from the setup script.")
            return
        self.install_btn.configure(state="disabled")
        threading.Thread(target=self._run_install, daemon=True).start()

    def _run_install(self):
        try:
            install_dir = Path(self.install_dir.get())
            cloud_url = self.cloud_url.get().strip().rstrip("/")
            token = self.token.get().strip()
            src = payload_dir()

            if not src.exists():
                self._log(f"ERROR: bundled payload not found at {src}")
                messagebox.showerror(APP_NAME, "Installer payload missing -- this build is broken.")
                return

            self._log(f"Installing to {install_dir} ...")
            install_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, install_dir, dirs_exist_ok=True)
            exe = install_dir / "LanUsageMonitorAgent.exe"

            config = {
                "cloud_base_url": cloud_url,
                "enrollment_token": token,
                "report_interval_seconds": 180,
            }
            (install_dir / "config.json").write_text(json.dumps(config, indent=2))
            self._log("Wrote config.json")

            self._log("Registering Windows Service (auto-start)...")
            self._run([str(exe), "--startup", "auto", "install"])

            self._log("Configuring crash auto-restart...")
            self._run([
                "sc.exe", "failure", SERVICE_NAME,
                "reset=", "86400",
                "actions=", "restart/5000/restart/5000/restart/60000",
            ])

            self._log("Starting service...")
            result = self._run([str(exe), "start"])

            self._log("Registering Add/Remove Programs entry...")
            self._register_uninstaller(exe, install_dir)

            self._log("")
            if result.returncode == 0:
                self._log("=== Install complete -- agent is running ===")
                messagebox.showinfo(
                    APP_NAME,
                    "Install complete.\n\n"
                    "This PC will enroll itself and appear on the dashboard within a "
                    "few minutes -- nothing else to do here.",
                )
            else:
                self._log("=== Installed, but the service did not start -- check logs\\agent.log ===")
                messagebox.showwarning(
                    APP_NAME,
                    f"Installed, but the service failed to start.\nCheck {install_dir / 'logs' / 'agent.log'} for details.",
                )

        except Exception as e:
            self._log(f"FAILED: {e}")
            messagebox.showerror(APP_NAME, f"Install failed: {e}")
        finally:
            self.install_btn.configure(state="normal")

    def _register_uninstaller(self, exe: Path, install_dir: Path):
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "0.2.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "Route Tracker")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{exe}" uninstall-all')
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def main():
    if not is_admin():
        relaunch_elevated()
        return
    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
