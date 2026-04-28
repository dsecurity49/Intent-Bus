import time
import requests
import subprocess
import argparse
import os

# --- CONFIG ---
BASE_URL = "https://yourusername.pythonanywhere.com"
POLL_INTERVAL = 5

try:
    with open(os.path.expanduser("~/.apikey")) as f:
        API_KEY = f.read().strip()
except FileNotFoundError:
    print("[!] Error: ~/.apikey file not found.")
    exit(1)

ALLOWED_SYS_CMDS = {
    "storage": ["df", "-h"],
    "uptime": ["uptime"],
    "battery": ["termux-battery-status"]
}

def execute_action(goal, payload):
    if goal == "notify":
        msg = payload.get("message", "No content")
        subprocess.run(["termux-notification", "-t", "Bus Alert", "-c", msg])
    
    elif goal == "log":
        with open("bus_log.txt", "a") as f:
            f.write(f"{time.ctime()} | {payload}\n")
            
    elif goal == "sys":
        cmd_key = payload.get("cmd")
        if cmd_key in ALLOWED_SYS_CMDS:
            print(f"[*] Executing allowed system command: {cmd_key}")
            subprocess.run(ALLOWED_SYS_CMDS[cmd_key])
        else:
            print(f"[!] Blocked unauthorized system command request: {cmd_key}")

def run_worker(goal):
    print(f"[*] Intent Bus Worker Online | Goal: {goal}")
    headers = {"X-API-KEY": API_KEY, "Content-Type": "application/json"}

    while True:
        try:
            res = requests.post(f"{BASE_URL}/claim?goal={goal}", headers=headers, timeout=10)
            
            if res.status_code == 204:
                time.sleep(POLL_INTERVAL)
                continue
                
            if res.status_code == 200:
                job = res.json()
                job_id = job['id']
                print(f"[+] Processing {job_id}")
                
                execute_action(goal, job['payload'])
                
                fulfill_res = requests.post(f"{BASE_URL}/fulfill/{job_id}", headers=headers, timeout=10)
                if fulfill_res.status_code == 200:
                    print(f"[✓] Intent {job_id} fulfilled")
                else:
                    print(f"[!] Fulfill failed for {job_id}: HTTP {fulfill_res.status_code}")
                
        except Exception as e:
            print(f"[!] Error: {e}")
            
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal", required=True, help="The job type to poll for")
    args = parser.parse_args()
    run_worker(args.goal)
