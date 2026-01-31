#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orchids 账户导入脚本 - 从 orchids.txt 批量导入账户到 accounts-orchids.json
"""
import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def load_jwts_from_file(file_path: Path) -> list:
    """
    从文件中加载 JWT 列表
    
    Args:
        file_path: JWT 文件路径
    
    Returns:
        list: (clerk_id, jwt_token) 元组列表
    """
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        return []
    
    jwts = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释
            if line and not line.startswith('#'):
                # 解析格式: clerk_session_id####jwt_token
                if "####" in line:
                    clerk_id, jwt_token = line.split("####", 1)
                    jwts.append((clerk_id.strip(), jwt_token.strip()))
                else:
                    # 兼容旧格式（只有 JWT）
                    jwts.append((None, line))
    
    return jwts


def load_accounts_config(config_path: Path) -> dict:
    """
    加载现有的账户配置
    
    Args:
        config_path: 配置文件路径
    
    Returns:
        dict: 配置字典
    """
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        # 返回默认配置
        return {"accounts": []}


def save_accounts_config(config_path: Path, config: dict):
    """
    保存账户配置
    
    Args:
        config_path: 配置文件路径
        config: 配置字典
    """
    # 确保目录存在
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Saved configuration to: {config_path}")


def add_single_account(config: dict, jwt: str, name: str = None, clerk_id: str = None) -> bool:
    """
    添加单个账户
    
    Args:
        config: 配置字典
        jwt: JWT token
        name: 账户名称（可选）
        clerk_id: Clerk session ID（可选）
    
    Returns:
        bool: 是否成功添加
    """
    # 检查是否已存在
    for account in config.get('accounts', []):
        if account.get('client_jwt') == jwt:
            print(f"⚠️  Account already exists: {account.get('name')}")
            return False
    
    # 生成账户名称
    if not name:
        if clerk_id:
            # 使用 clerk_id 生成名称
            name = f"orchids_{clerk_id.replace('clerk_', '')}"
        else:
            account_count = len(config.get('accounts', []))
            name = f"orchids_{account_count + 1}"
    
    # 创建账户对象
    account = {
        "name": name,
        "enabled": True,
        "client_jwt": jwt,
        "last_refreshed": None,
        "status": "active",
        "health": "unknown"
    }
    
    # 添加到配置
    if 'accounts' not in config:
        config['accounts'] = []
    
    config['accounts'].append(account)
    print(f"✅ Added account: {name}")
    
    return True


def import_all_accounts(
    jwt_file: Path,
    config_path: Path,
    limit: int = None,
    skip_existing: bool = True
):
    """
    从文件批量导入账户
    
    Args:
        jwt_file: JWT 文件路径
        config_path: 配置文件路径
        limit: 最多导入数量（None 表示全部）
        skip_existing: 是否跳过已存在的账户
    """
    print("=" * 60)
    print("Orchids Account Import Tool")
    print("=" * 60)
    
    # 加载 JWT 列表
    print(f"\n📂 Loading JWTs from: {jwt_file}")
    jwts = load_jwts_from_file(jwt_file)
    
    if not jwts:
        print("❌ No JWTs found in file")
        return
    
    print(f"✅ Found {len(jwts)} JWTs")
    
    # 应用限制
    if limit and limit > 0:
        jwts = jwts[:limit]
        print(f"ℹ️  Limiting to first {limit} accounts")
    
    # 加载现有配置
    print(f"\n📂 Loading existing configuration from: {config_path}")
    config = load_accounts_config(config_path)
    existing_count = len(config.get('accounts', []))
    print(f"ℹ️  Existing accounts: {existing_count}")
    
    # 导入账户
    print(f"\n🔄 Importing accounts...")
    added_count = 0
    skipped_count = 0
    
    for i, (clerk_id, jwt) in enumerate(jwts, 1):
        # 使用 clerk_id 生成名称（如果有）
        if clerk_id:
            name = f"orchids_{clerk_id.replace('clerk_', '')}"
        else:
            name = f"orchids_{existing_count + i}"
        
        if add_single_account(config, jwt, name, clerk_id):
            added_count += 1
        else:
            skipped_count += 1
    
    # 保存配置
    if added_count > 0:
        print(f"\n💾 Saving configuration...")
        save_accounts_config(config_path, config)
    
    # 显示统计
    print("\n" + "=" * 60)
    print("Import Summary")
    print("=" * 60)
    print(f"Total JWTs processed: {len(jwts)}")
    print(f"Accounts added: {added_count}")
    print(f"Accounts skipped: {skipped_count}")
    print(f"Total accounts now: {len(config.get('accounts', []))}")
    print("=" * 60)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Import Orchids accounts from JWT file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Import all accounts from orchids.txt
  python scripts/add_orchids_account.py --import-all

  # Import first 10 accounts
  python scripts/add_orchids_account.py --import-all --limit 10

  # Add a single account
  python scripts/add_orchids_account.py --jwt "eyJhbGc..." --name "my_account"

  # Specify custom paths
  python scripts/add_orchids_account.py --import-all \\
    --jwt-file /path/to/jwts.txt \\
    --config /path/to/accounts-orchids.json
        """
    )
    
    parser.add_argument(
        '--import-all',
        action='store_true',
        help='Import all accounts from JWT file'
    )
    
    parser.add_argument(
        '--jwt',
        type=str,
        help='Single JWT token to add'
    )
    
    parser.add_argument(
        '--name',
        type=str,
        help='Account name (for single JWT)'
    )
    
    parser.add_argument(
        '--jwt-file',
        type=str,
        default='config/orchids.txt',
        help='Path to JWT file (default: config/orchids.txt)'
    )
    
    parser.add_argument(
        '--config',
        type=str,
        default='config/accounts-orchids.json',
        help='Path to accounts config (default: config/accounts-orchids.json)'
    )
    
    parser.add_argument(
        '--limit',
        type=int,
        help='Maximum number of accounts to import'
    )
    
    args = parser.parse_args()
    
    # 转换为 Path 对象
    jwt_file = Path(args.jwt_file)
    config_path = Path(args.config)
    
    # 确保路径是绝对路径
    if not jwt_file.is_absolute():
        jwt_file = project_root / jwt_file
    if not config_path.is_absolute():
        config_path = project_root / config_path
    
    # 执行操作
    if args.import_all:
        # 批量导入
        import_all_accounts(jwt_file, config_path, args.limit)
    
    elif args.jwt:
        # 添加单个账户
        print("=" * 60)
        print("Adding Single Account")
        print("=" * 60)
        
        config = load_accounts_config(config_path)
        
        if add_single_account(config, args.jwt, args.name):
            save_accounts_config(config_path, config)
            print("\n✅ Account added successfully")
        else:
            print("\n⚠️  Account not added (already exists)")
    
    else:
        parser.print_help()
        print("\n❌ Error: Please specify --import-all or --jwt")
        sys.exit(1)


if __name__ == "__main__":
    main()
