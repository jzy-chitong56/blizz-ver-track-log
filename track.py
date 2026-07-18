#!/usr/bin/env python3
"""
BlizzTrack 多游戏版本追踪脚本
配置驱动：每个游戏一个 YAML 配置文件，放在 configs/ 目录下
支持单独运行某个配置，或一次运行所有配置
"""
import argparse
import os
import re
import glob
import sys
import yaml
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def load_config(config_path):
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["_path"] = config_path
    return config


def fetch_page(url):
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def parse_field_raw(html, field_config):
    """根据字段的 regex 从 HTML 中提取原始字符串"""
    regex = field_config["regex"]
    match = re.search(regex, html)
    if not match:
        raise ValueError(f"Failed to parse field with regex: {regex}")
    return match.group(1).strip()


def parse_time(raw_value, field_config):
    """将时间原始值转换为配置的目标时区和格式"""
    source = field_config.get("source", "raw")

    if source == "unix_timestamp_utc":
        timestamp = int(raw_value)
        dt = datetime.fromtimestamp(timestamp, timezone.utc)
        tz_name = field_config.get("timezone", "Asia/Shanghai")
        dt = dt.astimezone(ZoneInfo(tz_name))
        fmt = field_config.get("format", "%Y-%m-%d %H:%M")
        return dt.strftime(fmt)

    if source == "raw":
        return raw_value

    raise ValueError(f"Unsupported time source: {source}")


def read_log(log_file):
    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def has_new_data(log_content, version_value):
    """根据版本号判断是否有新数据：取日志第一行，看是否包含当前版本号"""
    if not log_content:
        return True
    first_line = log_content.strip().split("\n")[0]
    return version_value not in first_line


def write_log(log_file, log_content, entry):
    """新条目写入第一行，原内容下移"""
    new_content = f"{entry}\n{log_content}" if log_content else entry
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(new_content)


def track(config):
    """执行单个游戏的追踪流程"""
    name = config["name"]
    print(f"=== Tracking: {name} ===")

    html = fetch_page(config["url"])

    fields = {}
    version_value = None
    for field_name, field_config in config["fields"].items():
        raw_value = parse_field_raw(html, field_config)
        if field_config.get("is_time", False):
            fields[field_name] = parse_time(raw_value, field_config)
        else:
            fields[field_name] = raw_value
        # 用 role=version 标记的字段作为判断新数据依据
        if field_config.get("role") == "version":
            version_value = raw_value

    if version_value is None:
        # 兜底：把名为 version 的字段当作版本号
        version_value = fields.get("version", "")

    log_format = config.get("log_format", "{time}    {version}")
    entry = log_format.format(**fields)

    log_file = config["log_file"]
    log_content = read_log(log_file)

    if has_new_data(log_content, version_value):
        write_log(log_file, log_content, entry)
        print(f"Added new entry: {entry}")
    else:
        print(f"No new version detected. Current: {version_value}")


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
