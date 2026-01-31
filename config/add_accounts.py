#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量添加账号到 accounts.json
支持两种模式：
1. 交互式添加：python add_accounts.py
2. 从文件导入：python add_accounts.py tokens.txt（每行一个 refresh_token）
"""
import json
import sys
from pathlib import Path


def load_accounts_config(config_path: Path) -> dict:
    """加载现有的账号配置"""
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return {"accounts": []}


def save_accounts_config(config_path: Path, config: dict):
    """保存账号配置"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"✅ Configuration saved to: {config_path}")


def get_next_account_number(config: dict) -> int:
    """获取下一个账号编号"""
    accounts = config.get("accounts", [])
    max_num = 0
    for acc in accounts:
        name = acc.get("name", "")
        if name.startswith("account_"):
            try:
                num = int(name.split("_")[1])
                max_num = max(max_num, num)
            except (IndexError, ValueError):
                pass
    return max_num + 1


def add_account_interactive(config: dict):
    """交互式添加单个账号"""
    print("\n" + "=" * 60)
    print("Add New Account")
    print("=" * 60)
    
    # 获取账号名称
    default_name = f"account_{get_next_account_number(config)}"
    name = input(f"Account name (default: {default_name}): ").strip()
    if not name:
        name = default_name
    
    # 检查账号名称是否已存在
    existing_names = [acc.get("name") for acc in config.get("accounts", [])]
    if name in existing_names:
        print(f"❌ Account '{name}' already exists!")
        return False
    
    # 获取 refresh_token
    refresh_token = input("Refresh token: ").strip()
    if not refresh_token:
        print("❌ Refresh token cannot be empty!")
        return False
    
    # 获取是否启用
    enabled_input = input("Enabled? (Y/n): ").strip().lower()
    enabled = enabled_input != 'n'
    
    # 添加账号
    new_account = {
        "name": name,
        "refresh_token": refresh_token,
        "enabled": enabled
    }
    
    config.setdefault("accounts", []).append(new_account)
    print(f"✅ Account '{name}' added successfully!")
    return True


def add_accounts_from_file(config: dict, file_path: Path) -> int:
    """从文件批量导入账号"""
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return 0
    
    print(f"\n📁 Reading tokens from: {file_path}")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 过滤空行和注释
    tokens = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#'):
            tokens.append(line)
    
    if not tokens:
        print("❌ No valid tokens found in file!")
        return 0
    
    print(f"📊 Found {len(tokens)} tokens")
    
    # 获取现有账号名称和 token（避免重复）
    existing_names = [acc.get("name") for acc in config.get("accounts", [])]
    existing_tokens = [acc.get("refresh_token") for acc in config.get("accounts", [])]
    
    # 批量添加
    added_count = 0
    skipped_count = 0
    start_num = get_next_account_number(config)
    
    for i, token in enumerate(tokens):
        # 检查 token 是否已存在
        if token in existing_tokens:
            print(f"⚠️  Skipping duplicate token: {token[:30]}...")
            skipped_count += 1
            continue
        
        account_name = f"account_{start_num + added_count}"
        
        # 跳过已存在的账号名（理论上不会发生）
        if account_name in existing_names:
            print(f"⚠️  Skipping '{account_name}' (already exists)")
            skipped_count += 1
            continue
        
        new_account = {
            "name": account_name,
            "refresh_token": token,
            "enabled": True
        }
        
        config.setdefault("accounts", []).append(new_account)
        print(f"✅ Added: {account_name}")
        added_count += 1
    
    if skipped_count > 0:
        print(f"\n⚠️  Skipped {skipped_count} duplicate/invalid tokens")
    
    return added_count


def main():
    """主函数"""
    # 配置文件路径（在 config 目录下）
    config_path = Path(__file__).parent / "accounts.json"
    
    # 加载现有配置
    config = load_accounts_config(config_path)
    existing_count = len(config.get("accounts", []))
    print(f"📊 Current accounts: {existing_count}")
    
    # 检查是否指定了文件
    if len(sys.argv) > 1:
        # 从文件导入模式
        file_path = Path(sys.argv[1])
        # 如果是相对路径，相对于当前工作目录
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        
        added_count = add_accounts_from_file(config, file_path)
        
        if added_count > 0:
            save_accounts_config(config_path, config)
            print(f"\n✅ Successfully imported {added_count} accounts!")
            print(f"📊 Total accounts: {len(config.get('accounts', []))}")
        else:
            print("\n❌ No accounts were added.")
    else:
        # 交互式添加模式
        print("\n💡 Tip: You can also import from file:")
        print("   python add_accounts.py tokens.txt")
        print("   (Each line should contain one refresh_token)")
        
        while True:
            if add_account_interactive(config):
                save_accounts_config(config_path, config)
            
            # 询问是否继续
            continue_input = input("\nAdd another account? (y/N): ").strip().lower()
            if continue_input != 'y':
                break
        
        print(f"\n📊 Total accounts: {len(config.get('accounts', []))}")
        print("✅ Done!")


if __name__ == "__main__":
    main()
