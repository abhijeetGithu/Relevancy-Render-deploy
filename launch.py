#!/usr/bin/env python3
"""
Unified launcher for the Search Relevancy Suite.

Starts all three applications plus the portal with a single command:
    python launch.py

Services:
    Portal           -> http://localhost:8000
    DataQuery_Engine -> http://localhost:5050
    LLM_Comparator   -> http://localhost:8001
    relevancy-script -> http://localhost:8002
"""

import os
import signal
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICES = [
    {
        "name": "DataQuery_Engine",
        "cmd": [sys.executable, "server.py"],
        "cwd": os.path.join(BASE_DIR, "DataQuery_Engine"),
        "port": 5050,
        "env": {"DATAQUERY_NO_RELOADER": "1"},
    },
    {
        "name": "LLM_Comparator",
        "cmd": [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"],
        "cwd": os.path.join(BASE_DIR, "LLM_Comparator", "LLM_Comparator"),
        "port": 8001,
    },
    {
        "name": "relevancy-script",
        "cmd": [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"],
        "cwd": os.path.join(BASE_DIR, "relevancy-script"),
        "port": 8002,
    },
    {
        "name": "Portal",
        "cmd": [sys.executable, "-m", "uvicorn", "portal.app:app", "--host", "0.0.0.0", "--port", "8000"],
        "cwd": BASE_DIR,
        "port": 8000,
    },
]

processes: list[subprocess.Popen] = []


def start_all():
    for svc in SERVICES:
        cwd = svc["cwd"]
        if not os.path.isdir(cwd):
            print(f"  [SKIP] {svc['name']} — directory not found: {cwd}")
            continue
        print(f"  Starting {svc['name']} on port {svc['port']}...")
        env = os.environ.copy()
        env.update(svc.get("env", {}))
        proc = subprocess.Popen(
            svc["cmd"],
            cwd=cwd,
            stdout=None,
            stderr=None,
            env=env,
        )
        processes.append(proc)
        svc["proc"] = proc


def stop_all():
    print("\n  Shutting down all services...")
    for proc in processes:
        try:
            proc.terminate()
        except Exception:
            pass
    deadline = time.time() + 5
    for proc in processes:
        remaining = max(0, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("  All services stopped.")


def print_banner():
    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║         Search Relevancy Suite — Launcher        ║")
    print("  ╠══════════════════════════════════════════════════╣")
    print("  ║                                                  ║")
    print("  ║   Portal           http://localhost:8000         ║")
    print("  ║   DataQuery_Engine http://localhost:5050         ║")
    print("  ║   LLM_Comparator   http://localhost:8001         ║")
    print("  ║   relevancy-script http://localhost:8002         ║")
    print("  ║                                                  ║")
    print("  ║   Press Ctrl+C to stop all services              ║")
    print("  ║   Service logs appear below as you use the UI   ║")
    print(" ══════╝")
    print()


def main():
    signal.signal(signal.SIGINT, lambda *_: None)
    signal.signal(signal.SIGTERM, lambda *_: None)

    print()
    print("  Launching Search Relevancy Suite...")
    print()
    start_all()
    time.sleep(2)
    print_banner()

    try:
        while True:
            for svc in SERVICES:
                proc = svc.get("proc")
                if proc and proc.poll() is not None:
                    print(f"  [RESTART] {svc['name']} exited (code {proc.returncode}), restarting...")
                    env = os.environ.copy()
                    env.update(svc.get("env", {}))
                    new_proc = subprocess.Popen(
                        svc["cmd"],
                        cwd=svc["cwd"],
                        stdout=None,
                        stderr=None,
                        env=env,
                    )
                    idx = processes.index(proc)
                    processes[idx] = new_proc
                    svc["proc"] = new_proc
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        stop_all()


if __name__ == "__main__":
    main()
