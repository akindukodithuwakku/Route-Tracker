"""
"LAN Usage Monitor - Agent Setup" -- a self-contained installer GUI.

Built by PyInstaller into a single ClientAgentSetup.exe that embeds the
whole agent payload (LanUsageMonitorAgent.exe + its files, including the
bundled WinDivert driver) as data. When the user double-clicks it, this
script:
  1. Relaunches itself elevated (UAC prompt) if not already admin
  2. Asks for the manager's address, this PC's client_id + API key (from the
     manager's config\\clients.json), and optionally the manager's cert.pem
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

APP_NAME = "LAN Usage Monitor - Agent"
SERVICE_NAME = "LanUsageMonitorAgent"
DEFAULT_INSTALL_DIR = r"C:\Program Files\LAN Usage Monitor\Agent"
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
        root.geometry("560x540")
        root.resizable(False, False)

        tk.Label(root, text=APP_NAME, font=("Segoe UI", 14, "bold")).pack(pady=(16, 4))
        tk.Label(
            root,
            text="Installs the agent that reports this PC's internet usage\n"
                 "to your manager PC's dashboard. You'll need the manager's\n"
                 "address and this PC's client_id/api_key from the manager.",
            justify="center",
        ).pack(pady=(0, 12))

        form = tk.Frame(root)
        form.pack(fill="x", padx=24)

        tk.Label(form, text="Install to:").grid(row=0, column=0, sticky="w", pady=4)
        self.install_dir = tk.StringVar(value=DEFAULT_INSTALL_DIR)
        tk.Entry(form, textvariable=self.install_dir, width=40).grid(row=0, column=1, sticky="w")
        tk.Button(form, text="Browse...", command=self._browse_dir).grid(row=0, column=2, padx=6)

        tk.Label(form, text="Manager address (host:port):").grid(row=1, column=0, sticky="w", pady=4)
        self.manager_addr = tk.StringVar(value="192.168.1.10:8443")
        tk.Entry(form, textvariable=self.manager_addr, width=40).grid(row=1, column=1, sticky="w", columnspan=2)

        tk.Label(form, text="Client ID:").grid(row=2, column=0, sticky="w", pady=4)
        self.client_id = tk.StringVar(value="pc1")
        tk.Entry(form, textvariable=self.client_id, width=40).grid(row=2, column=1, sticky="w", columnspan=2)

        tk.Label(form, text="API key:").grid(row=3, column=0, sticky="w", pady=4)
        self.api_key = tk.StringVar(value="")
        tk.Entry(form, textvariable=self.api_key, width=40).grid(row=3, column=1, sticky="w", columnspan=2)

        tk.Label(form, text="Manager cert.pem (optional):").grid(row=4, column=0, sticky="w", pady=4)
        self.cert_path = tk.StringVar(value="")
        tk.Entry(form, textvariable=self.cert_path, width=40).grid(row=4, column=1, sticky="w")
        tk.Button(form, text="Browse...", command=self._browse_cert).grid(row=4, column=2, padx=6)

        tk.Label(
            root,
            text="If you skip the cert, the agent will still connect but won't verify the\n"
                 "manager's identity -- fine on a trusted LAN, not recommended otherwise.",
            fg="#666", font=("Segoe UI", 8), justify="center",
        ).pack(pady=(4, 0))

        self.install_btn = tk.Button(root, text="Install", width=16, command=self._start_install)
        self.install_btn.pack(pady=12)

        self.log = scrolledtext.ScrolledText(root, height=12, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=24, pady=(0, 16))

    def _browse_dir(self):
        chosen = filedialog.askdirectory()
        if chosen:
            self.install_dir.set(str(Path(chosen) / "LAN Usage Monitor" / "Agent"))

    def _browse_cert(self):
        chosen = filedialog.askopenfilename(filetypes=[("PEM certificate", "*.pem"), ("All files", "*.*")])
        if chosen:
            self.cert_path.set(chosen)

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
        addr = self.manager_addr.get().strip()
        api_key = self.api_key.get().strip()
        if not addr or ":" not in addr:
            messagebox.showerror(APP_NAME, "Enter the manager address as host:port, e.g. 192.168.1.10:8443")
            return
        if not api_key:
            messagebox.showerror(APP_NAME, "Enter the API key from the manager's config\\clients.json")
            return
        self.install_btn.configure(state="disabled")
        threading.Thread(target=self._run_install, daemon=True).start()

    def _run_install(self):
        try:
            install_dir = Path(self.install_dir.get())
            addr = self.manager_addr.get().strip()
            host, port = addr.rsplit(":", 1)
            src = payload_dir()

            if not src.exists():
                self._log(f"ERROR: bundled payload not found at {src}")
                messagebox.showerror(APP_NAME, "Installer payload missing -- this build is broken.")
                return

            self._log(f"Installing to {install_dir} ...")
            install_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, install_dir, dirs_exist_ok=True)
            exe = install_dir / "LanUsageMonitorAgent.exe"

            cert_path = self.cert_path.get().strip()
            ca_cert_path = ""
            if cert_path:
                self._log("Copying manager certificate...")
                shutil.copy(cert_path, install_dir / "cert.pem")
                ca_cert_path = "cert.pem"

            config = {
                "client_id": self.client_id.get().strip(),
                "api_key": self.api_key.get().strip(),
                "manager_url": f"https://{host}:{port}/api/report",
                "verify_ssl": bool(ca_cert_path),
                "ca_cert_path": ca_cert_path,
                "report_interval_seconds": 30,
                "exclude_remote_ports": [int(port)],
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
                messagebox.showinfo(APP_NAME, "Install complete. The agent is now running and reporting to the manager.")
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
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "0.1.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "LAN Usage Monitor")
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
