#!/usr/bin/env python3
"""
BlizzTrack 多游戏版本追踪脚本
配置驱动：每个游戏一个 YAML 配置文件，放在 configs/ 目录下
支持同时追踪同一游戏的多个 section（如 current / previous），按时间顺序合并写入日志
"""
import argparse
import os
import re
import glob
import sys
import time
import random
import yaml
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "configs")
LOG_DIR = os.path.join(BASE_DIR, "Ver Logs")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["_path"] = config_path
    return config


def fetch_page(url):
    headers = {
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    sep = "&" if "?" in url else "?"
    nocache_url = f"{url}{sep}_nocache={int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    response = requests.get(nocache_url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_json(url):
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Cache-Control": "no-cache, no-store, must-revalidate",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_field_raw(html, field_config):
    """根据字段的 regex 从 HTML 中提取原始字符串（只取第一个匹配）"""
    regex = field_config["regex"]
    match = re.search(regex, html)
    if not match:
        raise ValueError(f"Failed to parse field with regex: {regex}")
    return match.group(1).strip()


def parse_time(raw_value, field_config):
    """将时间原始值转换为目标格式，并返回 (time_str, sort_key)"""
    source = field_config.get("source", "raw")
    fmt = field_config.get("format", "%Y-%m-%d %H:%M")

    if source == "unix_timestamp_utc":
        timestamp = int(raw_value)
        dt = datetime.fromtimestamp(timestamp, timezone.utc)
        tz_name = field_config.get("timezone", "Asia/Shanghai")
        dt_local = dt.astimezone(ZoneInfo(tz_name))
        return dt_local.strftime(fmt), timestamp

    if source == "raw":
        dt = datetime.strptime(raw_value, fmt)
        return raw_value, dt.timestamp()

    raise ValueError(f"Unsupported time source: {source}")


def resolve_log_file(log_file):
    """解析日志文件路径：如果不是绝对路径，则相对于 LOG_DIR"""
    if os.path.isabs(log_file):
        return log_file
    return os.path.join(LOG_DIR, log_file)


def parse_log_line(line, time_format, tz_name="Asia/Shanghai"):
    """从日志行解析出 (time_str, version, sort_key)，解析失败返回 None"""
    line = line.strip()
    if not line:
        return None

    fmt_len = len(datetime.now().strftime(time_format))
    if len(line) < fmt_len:
        return None

    time_str = line[:fmt_len]
    try:
        dt = datetime.strptime(time_str, time_format)
        # 日志里的时间是目标时区（如北京时间），不是 UTC
        dt_localized = dt.replace(tzinfo=ZoneInfo(tz_name))
        sort_key = dt_localized.timestamp()
    except ValueError:
        return None

    version = line[fmt_len:].strip()
    return time_str, version, sort_key


def read_log(log_file, time_format):
    """读取日志，返回 [(time_str, version, sort_key), ...]，按文件中的降序排列"""
    log_path = resolve_log_file(log_file)
    if not os.path.exists(log_path):
        return []

    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            parsed = parse_log_line(line, time_format)
            if parsed:
                entries.append(parsed)
    return entries


def write_log(log_file, entries, log_format):
    """按时间降序写入日志"""
    log_path = resolve_log_file(log_file)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # 按 (时间, 版本号) 去重，同一时间同一版本只保留一条
    # 不同时间即使版本号相同也都保留（网站可能在不同时间推送了同一版本）
    seen = set()
    unique_entries = []
    for time_str, version, sort_key in entries:
        key = (time_str, version)
        if key in seen:
            continue
        seen.add(key)
        unique_entries.append((time_str, version, sort_key))

    unique_entries.sort(key=lambda x: x[2], reverse=True)

    lines = []
    for time_str, version, _ in unique_entries:
        line = log_format.format(time=time_str, version=version)
        lines.append(line)

    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        if lines:
            f.write("\n")


def parse_section(html, section_config, time_format):
    """解析单个 section，返回 (time_str, version, sort_key)"""
    raw_time = parse_field_raw(html, section_config["time"])
    time_str, sort_key = parse_time(raw_time, section_config["time"])
    version = parse_field_raw(html, section_config["version"]).strip()
    return time_str, version, sort_key


def fetch_from_api(config, time_format):
    """从 API 获取版本数据，返回 [(time_str, version, sort_key), ...]"""
    tact = config["tact"]
    tz_name = config.get("timezone", "Asia/Shanghai")

    # 1. 获取最新版本（Current Data）
    latest_url = f"https://blizztrack.com/api/manifest/{tact}/versions"
    latest_data = fetch_json(latest_url)
    result = latest_data["result"]

    entries = []

    # 解析最新版本
    if result["data"]:
        # 取第一个 region（和之前 HTML 抓取逻辑一致：分区时取第一个）
        first_region = result["data"][0]
        version = first_region["version_name"].strip()
        created_at = result["created_at"]  # ISO 格式 UTC 时间
        dt_utc = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        dt_local = dt_utc.astimezone(ZoneInfo(tz_name))
        time_str = dt_local.strftime(time_format)
        sort_key = dt_utc.timestamp()
        entries.append((time_str, version, sort_key))
        print(f"  [current] {time_str}    {version}")

    # 2. 获取上一个版本（Previous Data）——从 seqn 列表取第二个
    seqn_url = f"https://blizztrack.com/api/manifest/{tact}/seqn?file=versions&per_page=2"
    seqn_data = fetch_json(seqn_url)
    results = seqn_data["result"]["results"]

    if len(results) >= 2:
        prev_seqn = results[1]
        prev_ver_url = f"https://blizztrack.com/api/manifest/{tact}/versions?seqn={prev_seqn['seqn']}"
        try:
            prev_ver_data = fetch_json(prev_ver_url)
            prev_result = prev_ver_data["result"]
            if prev_result["data"]:
                first_region = prev_result["data"][0]
                version = first_region["version_name"].strip()
                created_at = prev_result["created_at"]
                dt_utc = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                dt_local = dt_utc.astimezone(ZoneInfo(tz_name))
                time_str = dt_local.strftime(time_format)
                sort_key = dt_utc.timestamp()
                entries.append((time_str, version, sort_key))
                print(f"  [previous] {time_str}    {version}")
        except Exception as e:
            print(f"  [previous] 获取失败: {e}")

    return entries


def track(config):
    """执行单个游戏的追踪流程"""
    name = config["name"]
    print(f"=== Tracking: {name} ===")

    log_format = config.get("log_format", "{time}    {version}")
    time_format = config.get("time_format", "%Y-%m-%d %H:%M")

    source = config.get("source", "html")

    if source == "api":
        new_entries = fetch_from_api(config, time_format)
    else:
        html = fetch_page(config["url"])
        new_entries = []
        sections = config.get("sections", {})
        if not sections:
            fields = config.get("fields", {})
            if "time" in fields and "version" in fields:
                sections = {"main": {"time": fields["time"], "version": fields["version"]}}

        for section_name, section_config in sections.items():
            try:
                entry = parse_section(html, section_config, time_format)
                new_entries.append(entry)
                print(f"  [{section_name}] {entry[0]}    {entry[1]}")
            except Exception as e:
                print(f"  [{section_name}] 解析失败: {e}")

    if not new_entries:
        print("没有解析到任何条目，跳过")
        return

    log_file = config["log_file"]
    existing_entries = read_log(log_file, time_format)
    all_entries = existing_entries + new_entries
    write_log(log_file, all_entries, log_format)

    existing_keys = {(e[0], e[1]) for e in existing_entries}
    added = [e for e in new_entries if (e[0], e[1]) not in existing_keys]
    if added:
        print(f"Added {len(added)} new entr{'y' if len(added) == 1 else 'ies'}:")
        for time_str, version, _ in sorted(added, key=lambda x: x[2], reverse=True):
            print(f"  {time_str}    {version}")
    else:
        print("No new entries detected")


def run_one(config_path):
    config = load_config(config_path)
    track(config)


def run_all(config_dir=CONFIG_DIR):
    if not os.path.isdir(config_dir):
        print(f"Config directory not found: {config_dir}")
        sys.exit(1)

    config_paths = sorted(glob.glob(os.path.join(config_dir, "*.yaml")) +
                          glob.glob(os.path.join(config_dir, "*.yml")))

    if not config_paths:
        print(f"No config files found in {config_dir}")
        sys.exit(1)

    failed = []
    for config_path in config_paths:
        try:
            run_one(config_path)
            print()
        except Exception as e:
            failed.append((config_path, e))
            print(f"Error tracking {config_path}: {e}\n")

    if failed:
        print(f"==== {len(failed)} config(s) failed ====")
        for path, err in failed:
            print(f"  - {path}: {err}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="BlizzTrack 多游戏版本追踪")
    parser.add_argument("--config", help="指定单个配置文件路径（不指定则运行 configs/ 下所有）")
    args = parser.parse_args()

    if args.config:
        run_one(args.config)
    else:
        run_all()


if __name__ == "__main__":
    main()
