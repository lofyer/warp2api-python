#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
迁移脚本：将旧的 accounts.json 迁移到新的单文件目录结构
"""
import json
import os
from pathlib import Path


def migrate_accounts():
    """迁移账户配置"""
    config_dir = Path(__file__).parent / "config"
    old_accounts_file = config_dir / "accounts.json"
    new_accounts_dir = config_dir / "accounts" / "warp"
    
    # 检查旧文件是否存在
    if not old_accounts_file.exists():
        print(f"旧配置文件不存在: {old_accounts_file}")
        return
    
    # 创建新目录
    new_accounts_dir.mkdir(parents=True, exist_ok=True)
    print(f"创建目录: {new_accounts_dir}")
    
    # 读取旧配置
    with open(old_accounts_file, 'r', encoding='utf-8') as f:
        old_config = json.load(f)
    
    accounts = old_config.get("accounts", [])
    print(f"找到 {len(accounts)} 个账户")
    
    # 迁移每个账户
    migrated = 0
    skipped = 0
    
    for acc in accounts:
        name = acc.get("name", "unknown")
        
        # 安全的文件名
        safe_name = name.replace("/", "_").replace("\\", "_")
        account_file = new_accounts_dir / f"{safe_name}.json"
        
        # 检查是否已存在
        if account_file.exists():
            print(f"  跳过 (已存在): {name}")
            skipped += 1
            continue
        
        # 构建新的账户配置
        new_config = {
            "name": name,
            "refresh_token": acc.get("refresh_token"),
            "enabled": acc.get("enabled", True)
        }
        
        # 可选字段
        if acc.get("status_code"):
            new_config["status_code"] = acc["status_code"]
        if acc.get("last_refreshed"):
            new_config["last_refreshed"] = acc["last_refreshed"]
        
        # 写入新文件
        with open(account_file, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=2, ensure_ascii=False)
        
        print(f"  迁移成功: {name} -> {account_file.name}")
        migrated += 1
    
    print(f"\n迁移完成!")
    print(f"  成功迁移: {migrated}")
    print(f"  跳过: {skipped}")
    print(f"  总计: {len(accounts)}")
    print(f"\n新账户目录: {new_accounts_dir}")
    
    # 提示备份旧文件
    backup_file = old_accounts_file.with_suffix('.json.bak')
    if not backup_file.exists():
        old_accounts_file.rename(backup_file)
        print(f"\n旧配置文件已备份为: {backup_file}")
    else:
        print(f"\n注意: 旧配置文件仍保留在 {old_accounts_file}")
        print(f"      如确认迁移成功，可手动删除")


if __name__ == "__main__":
    migrate_accounts()
