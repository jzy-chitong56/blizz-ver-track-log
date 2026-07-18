#!/usr/bin/env python3
import requests
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os

URL = "https://blizztrack.com/view/w3d?type=versions"
LOG_FILE = "Warcraft III Public Test Realm (Internal)"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def fetch_page():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    response = requests.get(URL, headers=headers)
    response.raise_for_status()
    return response.text


def parse_data(html):
    time_match = re.search(r'<time class="font-semibold" datetime="(\d+)">', html)
    if not time_match:
        raise ValueError("Failed to parse time from page")
    
    timestamp = int(time_match.group(1))
    dt = datetime.fromtimestamp(timestamp, timezone.utc)
    dt_beijing = dt.astimezone(BEIJING_TZ)

    version_match = re.search(r'<div class="text-sm font-medium text-gray-600 truncate font-semibold">\s*(\d+\.\d+\.\d+\.\d+)', html)
    if not version_match:
        raise ValueError("Failed to parse version from page")
    
    version = version_match.group(1).strip()

    return dt_beijing, version


def read_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def has_new_data(log_content, version):
    if not log_content:
        return True
    first_line = log_content.strip().split("\n")[0] if log_content else ""
    return version not in first_line


def write_log(log_content, dt, version):
    timestamp = dt.strftime("%Y-%m-%d %H:%M")
    new_entry = f"{timestamp} {version}"

    if log_content:
        new_content = f"{new_entry}\n{log_content}"
    else:
        new_content = new_entry

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"Added new entry: {new_entry}")


def main():
    try:
        html = fetch_page()
        dt_beijing, version = parse_data(html)
        log_content = read_log()

        if has_new_data(log_content, version):
            write_log(log_content, dt_beijing, version)
        else:
            print(f"No new version detected. Current: {version}")

    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
