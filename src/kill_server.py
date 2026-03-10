#!/usr/bin/env python3
"""
kill_server.py — Utility to free up the API port.
Reads config/gateway.json for the port and kills any process using it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

def get_api_port():
    try:
        base_dir = Path(__file__).parent.parent
        config_path = base_dir / "config" / "gateway.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("api", {}).get("port", 8002)
    except Exception:
        return 8002

def kill_port(port):
    print(f"Searching for process on port {port}...")
    try:
        # Get PID(s) using the port
        output = subprocess.check_output(["lsof", "-t", f"-i:{port}"], text=True)
        pids = output.strip().split("\n")
        
        if not pids or not pids[0]:
            print(f"✅ Port {port} is already free.")
            return

        for pid in pids:
            print(f"🔥 Killing process {pid} using port {port}...")
            subprocess.run(["kill", "-9", pid])
        
        print(f"🚀 Port {port} should be free now.")
    except subprocess.CalledProcessError:
        print(f"✅ No process found on port {port}.")
    except Exception as e:
        print(f"❌ Error while killing port: {e}")

if __name__ == "__main__":
    port = get_api_port()
    kill_port(port)
