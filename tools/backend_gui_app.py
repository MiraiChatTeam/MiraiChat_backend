from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import bcrypt
import customtkinter as ctk
import pyotp
import qrcode
import requests
import tkinter as tk
from cryptography.fernet import Fernet
from PIL import Image
from tkinter import messagebox


APP_TITLE = "Miraichat Backend Control Center"
BACKEND_LOGO_PATH = Path("res") / "new_dark_ios_opaque.png"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
DEFAULT_STORAGE_MB = 100
DEFAULT_LOG_LEVEL = "info"
WELCOME_NOTIFICATION_BODY = (
    "Welcome to MiraiChat! We're excited to have you here. "
    "If you run into bugs or hiccups, please share feedback so we can keep improving MiraiChat."
)

SURFACE = "#eef3f9"
CARD = "#fbfdff"
CARD_ALT = "#f4f7fb"
TEXT = "#132238"
MUTED = "#5f6f86"
ACCENT = "#2d6cdf"
ACCENT_SOFT = "#dce8ff"
SUCCESS = "#1f8f5f"
WARNING = "#cc7a00"
ERROR = "#c53a3a"
BORDER = "#d6dfea"

SUPPORTED_LANGUAGES = ["EN", "JP", "ZH-CN", "ZH-TW"]
LANGUAGE_ALIASES = {
    "ZH_CN": "ZH-CN",
    "ZH_TW": "ZH-TW",
}

CRYPTO_ASSETS: list[tuple[str, str]] = [
    ("usdt_trc20", "TEt8ww5Z76EmbRriLc6aNWwWFsjGFmgrLm"),
    ("usdt_spl", "7Ae74b9TAi5ue3dTe1d9PpH154JwEZZ8rwxnojVJVR8Q"),
    ("usdc_trc20", "TEt8ww5Z76EmbRriLc6aNWwWFsjGFmgrLm"),
    ("usdc_spl", "7Ae74b9TAi5ue3dTe1d9PpH154JwEZZ8rwxnojVJVR8Q"),
    ("btc", "bc1qjfevw4v005yzxtncaeh4z2us2p7jnpz3kz4qqe"),
    ("eth_erc20", "0xf1f8177fA841D38d086ddba78A930C655eC76792"),
    ("sol", "7Ae74b9TAi5ue3dTe1d9PpH154JwEZZ8rwxnojVJVR8Q"),
]
I18N: dict[str, dict[str, str]] = {
    "EN": {
        "mode_title": "Deployment mode",
        "mode_desc": "Choose your deployment profile.",
        "language_label": "Language",
        "simple_title": "Simple Mode",
        "simple_desc": "HTTPS and WebSocket will both route through the same Cloudflare Tunnel domain.",
        "simple_warning": "Simple Mode is recommended for deployments with fewer than 10 users.\nWebSocket over tunnel is less stable for large deployments.",
        "advanced_title": "Advanced Mode",
        "advanced_desc": "Expose HTTPS and WebSocket independently while tuning runtime and backend environment mapping.",
        "section_public_transport": "Public transport",
        "section_runtime": "Runtime",
        "section_storage": "Storage and quotas",
        "section_security": "Rate limits and security",
        "section_performance": "Performance",
        "section_legacy": "Legacy app access",
        "section_architecture": "Backend Architecture (Ports 8000 & 8001)",
        "header_subtitle": "Miraichat launcher for new_main:app with Simple and Advanced deployment flows.",
        "about_support_button": "About Us and Support",
        "about_support_title": "About Us and Support",
        "about_support_intro": "## Our mission\nWe believe private messaging should feel normal, not complicated. Our goal is to give everyone a calm and reliable place to talk, share, and stay connected.\n\n## What makes us different\nThe core of this app is to **build a private server you trust**. Unlike normal apps that keep your data on their servers, we provide the tools for you to own your infrastructure, making sure your privacy is never a promise, but a technical reality.\n\n## What we build\n- Simple tools to host your own trusted server\n- Fast messaging with clean, simple controls\n- Strong account protection for daily use\n- Privacy-first defaults without technical jargon\n\n## Technology you can trust\nBehind the scenes, we use modern encryption, secure local storage, and decentralized identity. Your conversations are designed to stay yours, while the app remains easy to use for everyone.\n\n## Why support matters\nYour support helps us keep improving stability, accessibility, and long-term security updates while keeping the project independent and user-focused.\n\n## Privacy\nWe only collect the minimum data needed to provide core messaging and security functions. For details, please read our privacy policy: https://homepage.miraichat.net/privacy/",
        "about_support_contact": "Contact us: miraichat@hotmail.com",
        "support_card_button": "Support via Card",
        "support_crypto_button": "Support via Crypto",
        "status_title": "Status",
        "status_desc": "Backend state is probed from the running process and transport descriptor endpoint.",
        "local_probe_title": "Local Probe",
        "local_probe_empty": "No status probe has completed yet.",
        "logs_title": "Logs",
        "crypto_modal_title": "Support via Crypto",
        "card_modal_title": "Support via Card",
        "crypto_select_asset": "Cryptocurrency",
        "crypto_wallet_address": "Wallet address",
        "copy_address": "Copy Address",
        "address_copied": "Address copied to clipboard.",
        "donation_qr_missing": "Unable to find donation.png in tools/assets.",
        "crypto_asset_usdt_trc20": "USDT (TRC20)",
        "crypto_asset_usdt_spl": "USDT (SPL)",
        "crypto_asset_usdc_trc20": "USDC (TRC20)",
        "crypto_asset_usdc_spl": "USDC (SPL)",
        "crypto_asset_btc": "BTC",
        "crypto_asset_eth_erc20": "ETH (ERC20)",
        "crypto_asset_sol": "SOL",
    },
    "JP": {
        "mode_title": "デプロイモード",
        "mode_desc": "デプロイプロファイルを選択してください。",
        "language_label": "言語",
        "simple_title": "シンプルモード",
        "simple_desc": "HTTPS と WebSocket は同じ Cloudflare Tunnel ドメイン経由でルーティングされます。",
        "simple_warning": "Simple Mode は 10 人未満の運用に推奨です。\n大規模運用ではトンネル経由の WebSocket が不安定になる場合があります。",
        "advanced_title": "アドバンスドモード",
        "advanced_desc": "HTTPS と WebSocket を独立公開し、実行時設定とバックエンド環境変数の対応を調整します。",
        "section_public_transport": "公開トランスポート",
        "section_runtime": "ランタイム",
        "section_storage": "ストレージと容量",
        "section_security": "レート制限とセキュリティ",
        "section_performance": "パフォーマンス",
        "section_legacy": "レガシーアプリ連携",
        "section_architecture": "バックエンド構成（ポート 8000 / 8001）",
        "header_subtitle": "Simple/Advanced デプロイを備えた new_main:app 向けランチャーです。",
        "about_support_button": "私たちについてとサポート",
        "about_support_title": "私たちについてとサポート",
        "about_support_intro": "## 私たちの想い\n私たちは、プライベートな連絡手段を「むずかしいもの」ではなく、誰でも安心して使える日常の道具にしたいと考えています。\n\n## 他のアプリとの違い\nこのアプリの核心は、**「自分が信頼できるプライベートサーバーを構築する」**ことにあります。データを運営会社のサーバーに預ける一般的なアプリとは異なり、自分自身のインフラを持つことで、プライバシーを「約束」ではなく「技術的な事実」として実現します。\n\n## このアプリで大切にしていること\n- 信頼できる自前サーバーを簡単に構築するツール\n- 速くて分かりやすいメッセージ体験\n- 日常利用に十分なアカウント保護\n- 専門知識がなくても使えるプライバシー設計\n\n## 技術面の取り組み\n内部では、現代的な暗号化、安全なローカル保存、分散型アイデンティティを採用しています。難しい設定を増やさずに、会話の安全性を高めることを重視しています。\n\n## サポートのお願い\nみなさまのサポートは、安定性向上・アクセシビリティ改善・継続的なセキュリティ更新に直接つながり、プロジェクトの独立性を支えます。\n\n## プライバシー\n私たちは、メッセージ機能とセキュリティ提供に必要な最小限のデータのみを扱います。詳細はプライバシーポリシーをご確認ください: https://homepage.miraichat.net/privacy/",
        "about_support_contact": "Contact us: miraichat@hotmail.com",
        "support_card_button": "カードでサポート",
        "support_crypto_button": "暗号資産でサポート",
        "status_title": "ステータス",
        "status_desc": "実行中プロセスと transport descriptor エンドポイントで状態を確認します。",
        "local_probe_title": "ローカルプローブ",
        "local_probe_empty": "まだステータスプローブは完了していません。",
        "logs_title": "ログ",
        "crypto_modal_title": "暗号資産でサポート",
        "card_modal_title": "カードでサポート",
        "crypto_select_asset": "暗号資産",
        "crypto_wallet_address": "ウォレットアドレス",
        "copy_address": "アドレスをコピー",
        "address_copied": "アドレスをコピーしました。",
        "donation_qr_missing": "tools/assets 内に donation.png が見つかりません。",
        "crypto_asset_usdt_trc20": "USDT (TRC20)",
        "crypto_asset_usdt_spl": "USDT (SPL)",
        "crypto_asset_usdc_trc20": "USDC (TRC20)",
        "crypto_asset_usdc_spl": "USDC (SPL)",
        "crypto_asset_btc": "BTC",
        "crypto_asset_eth_erc20": "ETH (ERC20)",
        "crypto_asset_sol": "SOL",
    },
    "ZH-CN": {
        "mode_title": "部署模式",
        "mode_desc": "请选择部署配置。",
        "language_label": "语言",
        "simple_title": "简单模式",
        "simple_desc": "HTTPS 和 WebSocket 将通过同一个 Cloudflare Tunnel 域名转发。",
        "simple_warning": "简单模式建议用于少于 10 人的部署。\n大规模部署时，隧道 WebSocket 稳定性会下降。",
        "advanced_title": "高级模式",
        "advanced_desc": "将 HTTPS 与 WebSocket 独立暴露，并细化运行参数与后端环境变量映射。",
        "section_public_transport": "公共传输",
        "section_runtime": "运行时",
        "section_storage": "存储与配额",
        "section_security": "限流与安全",
        "section_performance": "性能",
        "section_legacy": "旧版服务访问",
        "section_architecture": "后端架构（端口 8000 / 8001）",
        "header_subtitle": "面向 new_main:app 的启动器，支持简单与高级部署流程。",
        "about_support_button": "关于我们与支持",
        "about_support_title": "关于我们与支持",
        "about_support_intro": "## 我们的初心\n我们希望私密通信不再复杂，而是每个人都能轻松上手、放心使用的日常工具。\n\n## 我们的核心差异\n这款应用的关键在于**“构建一个你信任的私有服务器”**。不同于将数据存储在公司服务器上的普通应用，我们赋予你拥有自己基础设施的能力，确保隐私不仅是一个承诺，而是一个技术事实。\n\n## 我们在做什么\n- 让你轻松部署和管理信任服务器的工具\n- 快速、清晰、易理解的聊天体验\n- 面向日常场景的账户安全保护\n- 不需要专业知识的隐私默认设置\n\n## 技术与安全\n在底层，我们使用现代加密、安全本地存储和去中心化身份。目标是在不增加学习成本的前提下，让你的通信更可靠、更安心。\n\n## 为什么需要支持\n你的支持会直接用于稳定性优化、无障碍体验改进和长期安全更新，帮助项目保持独立并持续为用户创造价值。\n\n## 隐私\n我们仅收集提供核心消息与安全功能所必需的最少数据。更多信息请查看隐私政策：https://homepage.miraichat.net/privacy/",
        "about_support_contact": "Contact us: miraichat@hotmail.com",
        "support_card_button": "通过银行卡支持",
        "support_crypto_button": "通过加密货币支持",
        "status_title": "状态",
        "status_desc": "通过运行进程和 transport descriptor 端点探测后端状态。",
        "local_probe_title": "本地探测",
        "local_probe_empty": "尚未完成状态探测。",
        "logs_title": "日志",
        "crypto_modal_title": "通过加密货币支持",
        "card_modal_title": "通过银行卡支持",
        "crypto_select_asset": "加密货币",
        "crypto_wallet_address": "钱包地址",
        "copy_address": "复制地址",
        "address_copied": "地址已复制到剪贴板。",
        "donation_qr_missing": "在 tools/assets 中未找到 donation.png。",
        "crypto_asset_usdt_trc20": "USDT (TRC20)",
        "crypto_asset_usdt_spl": "USDT (SPL)",
        "crypto_asset_usdc_trc20": "USDC (TRC20)",
        "crypto_asset_usdc_spl": "USDC (SPL)",
        "crypto_asset_btc": "BTC",
        "crypto_asset_eth_erc20": "ETH (ERC20)",
        "crypto_asset_sol": "SOL",
    },
    "ZH-TW": {
        "mode_title": "部署模式",
        "mode_desc": "請選擇部署配置。",
        "language_label": "語言",
        "simple_title": "簡單模式",
        "simple_desc": "HTTPS 與 WebSocket 會透過同一個 Cloudflare Tunnel 網域轉送。",
        "simple_warning": "簡單模式建議用於少於 10 人的部署。\n大規模部署時，隧道 WebSocket 穩定性會下降。",
        "advanced_title": "進階模式",
        "advanced_desc": "將 HTTPS 與 WebSocket 分開公開，並細部調整執行參數與後端環境變數映射。",
        "section_public_transport": "公開傳輸",
        "section_runtime": "執行期",
        "section_storage": "儲存與配額",
        "section_security": "限流與安全",
        "section_performance": "效能",
        "section_legacy": "舊版服務存取",
        "section_architecture": "後端架構（連接埠 8000 / 8001）",
        "header_subtitle": "面向 new_main:app 的啟動器，支援簡單與進階部署流程。",
        "about_support_button": "關於我們與支持",
        "about_support_title": "關於我們與支持",
        "about_support_intro": "## 我們的初心\n我們希望私密通信不再複雜，而是每個人都能輕鬆上手、安心使用的日常工具。\n\n## 我們的核心差異\n這款應用的關鍵在於**「構建一個你信任的私有服務器」**。不同於將數據存儲在公司服務器上的普通應用，我們賦予你擁有自己基礎設施的能力，確保隱私不僅是一個承諾，而是一個技術事實。\n\n## 我們正在打造\n- 讓你輕鬆部署和管理信任服務器的工具\n- 快速、清楚、易理解的聊天體驗\n- 適合日常使用的帳戶安全保護\n- 不需專業背景的隱私預設\n\n## 技術與保護\n在底層，我們採用現代加密、安全本地儲存，以及去中心化身份。目標是在不增加使用負擔的前提下，讓你的通訊更可靠、更安心。\n\n## 為何需要支持\n你的支持會直接投入穩定性優化、無障礙體驗改進與長期安全更新，幫助專案保持獨立並持續為使用者創造價值。\n\n## 隱私\n我們僅收集提供核心訊息與安全功能所必需的最少資料。更多內容請參閱隱私政策：https://homepage.miraichat.net/privacy/",
        "about_support_contact": "Contact us: miraichat@hotmail.com",
        "support_card_button": "透過銀行卡支持",
        "support_crypto_button": "透過加密貨幣支持",
        "status_title": "狀態",
        "status_desc": "透過執行程序與 transport descriptor 端點探測後端狀態。",
        "local_probe_title": "本地探測",
        "local_probe_empty": "尚未完成狀態探測。",
        "logs_title": "日誌",
        "crypto_modal_title": "透過加密貨幣支持",
        "card_modal_title": "透過銀行卡支持",
        "crypto_select_asset": "加密貨幣",
        "crypto_wallet_address": "錢包地址",
        "copy_address": "複製地址",
        "address_copied": "地址已複製到剪貼簿。",
        "donation_qr_missing": "在 tools/assets 中找不到 donation.png。",
        "crypto_asset_usdt_trc20": "USDT (TRC20)",
        "crypto_asset_usdt_spl": "USDT (SPL)",
        "crypto_asset_usdc_trc20": "USDC (TRC20)",
        "crypto_asset_usdc_spl": "USDC (SPL)",
        "crypto_asset_btc": "BTC",
        "crypto_asset_eth_erc20": "ETH (ERC20)",
        "crypto_asset_sol": "SOL",
    },
}

UI_TEXT_TRANSLATIONS: dict[str, dict[str, str]] = {
    "JP": {
        "Cloudflare Tunnel domain": "Cloudflare Tunnel domain",
        "Example: https://chat.example.miraichat.net": "例: https://chat.example.miraichat.net",
        "Per-user storage size (MB)": "ユーザーごとのストレージ容量 (MB)",
        "Applied to DEFAULT_STORAGE_LIMIT and private-server storage defaults.": "DEFAULT_STORAGE_LIMIT と private-server の既定容量に適用されます。",
        "Public hub URL": "公開ハブ URL",
        "Used by federation and client metadata.": "フェデレーションとクライアントメタデータに使用されます。",
        "API public base URL": "API 公開ベース URL",
        "HTTPS endpoint advertised to clients.": "クライアントに公開される HTTPS エンドポイントです。",
        "WebSocket connection mode": "WebSocket 接続モード",
        "Choose tunnel for proxied WS or direct for a dedicated public WS endpoint.": "プロキシ経由の WS は tunnel、専用公開 WS は direct を選択してください。",
        "WS tunnel base URL": "WS トンネルベース URL",
        "Used when WS_CONNECTION_MODE=tunnel.": "WS_CONNECTION_MODE=tunnel のときに使用されます。",
        "WS direct public URL": "WS 直接公開 URL",
        "Used when WS_CONNECTION_MODE=direct.": "WS_CONNECTION_MODE=direct のときに使用されます。",
        "Reverse proxy profile": "リバースプロキシプロファイル",
        "Saved for operator clarity; does not alter backend code.": "運用者向けの記録です。バックエンドコードには影響しません。",
        "Optional reference field for operators using Cloudflare Tunnel.": "Cloudflare Tunnel を使う運用者向けの任意参照項目です。",
        "Backend host": "バックエンドホスト",
        "Host passed to uvicorn.": "uvicorn に渡されるホストです。",
        "Backend port": "バックエンドポート",
        "Port passed to uvicorn.": "uvicorn に渡されるポートです。",
        "Reload code on change": "変更時にコードをリロード",
        "Maps to uvicorn --reload.": "uvicorn --reload に対応します。",
        "Log level": "ログレベル",
        "Uvicorn log level.": "Uvicorn のログレベルです。",
        "Access log": "アクセスログ",
        "Disable if you want quieter terminal output.": "ターミナル出力を減らしたい場合は無効化してください。",
        "Default storage limit (MB)": "既定ストレージ上限 (MB)",
        "Writes DEFAULT_STORAGE_LIMIT in bytes for new users.": "新規ユーザー向けに DEFAULT_STORAGE_LIMIT (bytes) を設定します。",
        "Private server storage limit (MB)": "プライベートサーバー容量上限 (MB)",
        "Maps to PRIVATE_SERVER_STORAGE_LIMIT_MB.": "PRIVATE_SERVER_STORAGE_LIMIT_MB に対応します。",
        "Donation storage limit (MB)": "寄付プラン容量上限 (MB)",
        "Maps to DONATION_MONTHLY_STORAGE_LIMIT_MB.": "DONATION_MONTHLY_STORAGE_LIMIT_MB に対応します。",
        "Upload directory": "アップロードディレクトリ",
        "Relative or absolute path used by UPLOAD_DIR.": "UPLOAD_DIR で使用する相対/絶対パスです。",
        "File retention days": "ファイル保持日数",
        "Maps to FILE_RETENTION_DAYS.": "FILE_RETENTION_DAYS に対応します。",
        "Offline message retention days": "オフラインメッセージ保持日数",
        "Maps to OFFLINE_MSG_RETENTION_DAYS.": "OFFLINE_MSG_RETENTION_DAYS に対応します。",
        "Register rate limit max": "登録レート制限 最大回数",
        "Maps to REGISTER_RATE_LIMIT_MAX.": "REGISTER_RATE_LIMIT_MAX に対応します。",
        "Register rate limit window (seconds)": "登録レート制限 ウィンドウ (秒)",
        "Maps to REGISTER_RATE_LIMIT_WINDOW.": "REGISTER_RATE_LIMIT_WINDOW に対応します。",
        "Registration key": "登録キー",
        "Optional REGISTRATION_KEY for controlled onboarding.": "管理された登録導線用の任意 REGISTRATION_KEY です。",
        "Server identity salt": "サーバー識別 salt",
        "Optional SERVER_IDENTITY_SALT override.": "任意の SERVER_IDENTITY_SALT 上書き値です。",
        "Admin IP allowlist": "管理 IP 許可リスト",
        "Comma-separated ADMIN_IP_ALLOWLIST entries.": "カンマ区切りの ADMIN_IP_ALLOWLIST です。",
        "Treat this server as a public hub": "このサーバーを公開ハブとして扱う",
        "Maps to IS_PUBLIC_HUB.": "IS_PUBLIC_HUB に対応します。",
        "PIN sync auto default": "PIN 同期の自動既定",
        "Maps to PIN_SYNC_AUTO_DEFAULT.": "PIN_SYNC_AUTO_DEFAULT に対応します。",
        "Force manual PIN sync": "PIN 同期を手動に固定",
        "Maps to PIN_SYNC_FORCE_MANUAL.": "PIN_SYNC_FORCE_MANUAL に対応します。",
        "Push queue workers": "プッシュキューワーカー数",
        "Maps to PUSH_QUEUE_WORKERS.": "PUSH_QUEUE_WORKERS に対応します。",
        "Push queue max size": "プッシュキュー最大サイズ",
        "Maps to PUSH_QUEUE_MAX_SIZE.": "PUSH_QUEUE_MAX_SIZE に対応します。",
        "Presence backend": "Presence バックエンド",
        "Choose memory or redis.": "memory または redis を選択します。",
        "Presence Redis URL": "Presence Redis URL",
        "Required only when presence backend uses redis.": "presence backend が redis の場合のみ必要です。",
        "Fanout backend": "Fanout バックエンド",
        "Fanout Redis URL": "Fanout Redis URL",
        "Required only when fanout backend uses redis.": "fanout backend が redis の場合のみ必要です。",
        "Region policy mode": "リージョンポリシーモード",
        "Maps to REGION_POLICY_MODE.": "REGION_POLICY_MODE に対応します。",
        "Default region": "既定リージョン",
        "Maps to DEFAULT_REGION.": "DEFAULT_REGION に対応します。",
        "China FCM policy": "中国向け FCM ポリシー",
        "Maps to CHINA_FCM_POLICY.": "CHINA_FCM_POLICY に対応します。",
        "LEGACY_PUBLIC_URL": "LEGACY_PUBLIC_URL",
        "Optional public base URL used by the Open Legacy Docs button.": "Open Legacy Docs ボタンで使う任意の公開ベース URL です。",
        "The backend consists of two FastAPI services:\n• Migration backend (new_main:app) — port 8000\n• Legacy backend (legacy_app) — port 8001\nBoth are automatically started after TOTP verification.": "バックエンドは 2 つの FastAPI サービスで構成されています:\n• Migration backend (new_main:app) — port 8000\n• Legacy backend (legacy_app) — port 8001\nどちらも TOTP 認証後に自動起動します。",
        "Open Migration Docs (8000)": "Migration Docs を開く (8000)",
        "Open Legacy Docs (8001)": "Legacy Docs を開く (8001)",
        "Enabled": "有効",
    },
    "ZH-CN": {
        "Cloudflare Tunnel domain": "Cloudflare Tunnel 域名",
        "Example: https://chat.example.miraichat.net": "示例: https://chat.example.miraichat.net",
        "Per-user storage size (MB)": "每用户存储大小 (MB)",
        "Applied to DEFAULT_STORAGE_LIMIT and private-server storage defaults.": "应用到 DEFAULT_STORAGE_LIMIT 与私有服务器默认存储设置。",
        "Public hub URL": "公开 Hub URL",
        "Used by federation and client metadata.": "用于联邦与客户端元数据。",
        "API public base URL": "API 公开基础 URL",
        "HTTPS endpoint advertised to clients.": "向客户端公布的 HTTPS 端点。",
        "WebSocket connection mode": "WebSocket 连接模式",
        "Choose tunnel for proxied WS or direct for a dedicated public WS endpoint.": "代理 WS 请选择 tunnel，独立公开 WS 请选择 direct。",
        "WS tunnel base URL": "WS 隧道基础 URL",
        "Used when WS_CONNECTION_MODE=tunnel.": "当 WS_CONNECTION_MODE=tunnel 时使用。",
        "WS direct public URL": "WS 直连公开 URL",
        "Used when WS_CONNECTION_MODE=direct.": "当 WS_CONNECTION_MODE=direct 时使用。",
        "Reverse proxy profile": "反向代理配置",
        "Saved for operator clarity; does not alter backend code.": "用于运维说明，不会修改后端代码。",
        "Optional reference field for operators using Cloudflare Tunnel.": "供使用 Cloudflare Tunnel 的运维人员参考的可选字段。",
        "Backend host": "后端主机",
        "Host passed to uvicorn.": "传给 uvicorn 的主机。",
        "Backend port": "后端端口",
        "Port passed to uvicorn.": "传给 uvicorn 的端口。",
        "Reload code on change": "代码变更自动重载",
        "Maps to uvicorn --reload.": "对应 uvicorn --reload。",
        "Log level": "日志级别",
        "Uvicorn log level.": "Uvicorn 日志级别。",
        "Access log": "访问日志",
        "Disable if you want quieter terminal output.": "如需更少终端输出可禁用。",
        "Default storage limit (MB)": "默认存储上限 (MB)",
        "Writes DEFAULT_STORAGE_LIMIT in bytes for new users.": "为新用户写入 DEFAULT_STORAGE_LIMIT（字节）。",
        "Private server storage limit (MB)": "私有服务器存储上限 (MB)",
        "Maps to PRIVATE_SERVER_STORAGE_LIMIT_MB.": "对应 PRIVATE_SERVER_STORAGE_LIMIT_MB。",
        "Donation storage limit (MB)": "捐赠档位存储上限 (MB)",
        "Maps to DONATION_MONTHLY_STORAGE_LIMIT_MB.": "对应 DONATION_MONTHLY_STORAGE_LIMIT_MB。",
        "Upload directory": "上传目录",
        "Relative or absolute path used by UPLOAD_DIR.": "UPLOAD_DIR 使用的相对或绝对路径。",
        "File retention days": "文件保留天数",
        "Maps to FILE_RETENTION_DAYS.": "对应 FILE_RETENTION_DAYS。",
        "Offline message retention days": "离线消息保留天数",
        "Maps to OFFLINE_MSG_RETENTION_DAYS.": "对应 OFFLINE_MSG_RETENTION_DAYS。",
        "Register rate limit max": "注册限流最大次数",
        "Maps to REGISTER_RATE_LIMIT_MAX.": "对应 REGISTER_RATE_LIMIT_MAX。",
        "Register rate limit window (seconds)": "注册限流窗口（秒）",
        "Maps to REGISTER_RATE_LIMIT_WINDOW.": "对应 REGISTER_RATE_LIMIT_WINDOW。",
        "Registration key": "注册密钥",
        "Optional REGISTRATION_KEY for controlled onboarding.": "受控注册流程可选 REGISTRATION_KEY。",
        "Server identity salt": "服务器身份 salt",
        "Optional SERVER_IDENTITY_SALT override.": "可选 SERVER_IDENTITY_SALT 覆盖值。",
        "Admin IP allowlist": "管理员 IP 白名单",
        "Comma-separated ADMIN_IP_ALLOWLIST entries.": "逗号分隔的 ADMIN_IP_ALLOWLIST 条目。",
        "Treat this server as a public hub": "将此服务器视为公开 Hub",
        "Maps to IS_PUBLIC_HUB.": "对应 IS_PUBLIC_HUB。",
        "PIN sync auto default": "PIN 同步自动默认",
        "Maps to PIN_SYNC_AUTO_DEFAULT.": "对应 PIN_SYNC_AUTO_DEFAULT。",
        "Force manual PIN sync": "强制手动 PIN 同步",
        "Maps to PIN_SYNC_FORCE_MANUAL.": "对应 PIN_SYNC_FORCE_MANUAL。",
        "Push queue workers": "推送队列工作线程",
        "Maps to PUSH_QUEUE_WORKERS.": "对应 PUSH_QUEUE_WORKERS。",
        "Push queue max size": "推送队列最大容量",
        "Maps to PUSH_QUEUE_MAX_SIZE.": "对应 PUSH_QUEUE_MAX_SIZE。",
        "Presence backend": "Presence 后端",
        "Choose memory or redis.": "选择 memory 或 redis。",
        "Presence Redis URL": "Presence Redis URL",
        "Required only when presence backend uses redis.": "仅在 presence backend 使用 redis 时需要。",
        "Fanout backend": "Fanout 后端",
        "Fanout Redis URL": "Fanout Redis URL",
        "Required only when fanout backend uses redis.": "仅在 fanout backend 使用 redis 时需要。",
        "Region policy mode": "区域策略模式",
        "Maps to REGION_POLICY_MODE.": "对应 REGION_POLICY_MODE。",
        "Default region": "默认区域",
        "Maps to DEFAULT_REGION.": "对应 DEFAULT_REGION。",
        "China FCM policy": "中国 FCM 策略",
        "Maps to CHINA_FCM_POLICY.": "对应 CHINA_FCM_POLICY。",
        "LEGACY_PUBLIC_URL": "LEGACY_PUBLIC_URL",
        "Optional public base URL used by the Open Legacy Docs button.": "Open Legacy Docs 按钮使用的可选公开基础 URL。",
        "The backend consists of two FastAPI services:\n• Migration backend (new_main:app) — port 8000\n• Legacy backend (legacy_app) — port 8001\nBoth are automatically started after TOTP verification.": "后端由两个 FastAPI 服务组成:\n• Migration backend (new_main:app) — port 8000\n• Legacy backend (legacy_app) — port 8001\n两者都会在 TOTP 验证后自动启动。",
        "Open Migration Docs (8000)": "打开 Migration Docs (8000)",
        "Open Legacy Docs (8001)": "打开 Legacy Docs (8001)",
        "Enabled": "已启用",
    },
    "ZH-TW": {
        "Cloudflare Tunnel domain": "Cloudflare Tunnel 網域",
        "Example: https://chat.example.miraichat.net": "範例: https://chat.example.miraichat.net",
        "Per-user storage size (MB)": "每位使用者儲存大小 (MB)",
        "Applied to DEFAULT_STORAGE_LIMIT and private-server storage defaults.": "套用到 DEFAULT_STORAGE_LIMIT 與私有伺服器預設儲存設定。",
        "Public hub URL": "公開 Hub URL",
        "Used by federation and client metadata.": "用於聯邦與客戶端中繼資料。",
        "API public base URL": "API 公開基底 URL",
        "HTTPS endpoint advertised to clients.": "向客戶端公告的 HTTPS 端點。",
        "WebSocket connection mode": "WebSocket 連線模式",
        "Choose tunnel for proxied WS or direct for a dedicated public WS endpoint.": "代理 WS 請選 tunnel，獨立公開 WS 請選 direct。",
        "WS tunnel base URL": "WS 隧道基底 URL",
        "Used when WS_CONNECTION_MODE=tunnel.": "當 WS_CONNECTION_MODE=tunnel 時使用。",
        "WS direct public URL": "WS 直連公開 URL",
        "Used when WS_CONNECTION_MODE=direct.": "當 WS_CONNECTION_MODE=direct 時使用。",
        "Reverse proxy profile": "反向代理設定",
        "Saved for operator clarity; does not alter backend code.": "僅供維運辨識，不會更動後端程式碼。",
        "Optional reference field for operators using Cloudflare Tunnel.": "提供使用 Cloudflare Tunnel 的維運人員參考的可選欄位。",
        "Backend host": "後端主機",
        "Host passed to uvicorn.": "傳給 uvicorn 的主機。",
        "Backend port": "後端連接埠",
        "Port passed to uvicorn.": "傳給 uvicorn 的連接埠。",
        "Reload code on change": "程式變更自動重載",
        "Maps to uvicorn --reload.": "對應 uvicorn --reload。",
        "Log level": "日誌等級",
        "Uvicorn log level.": "Uvicorn 日誌等級。",
        "Access log": "存取日誌",
        "Disable if you want quieter terminal output.": "若希望終端輸出更少可停用。",
        "Default storage limit (MB)": "預設儲存上限 (MB)",
        "Writes DEFAULT_STORAGE_LIMIT in bytes for new users.": "為新使用者寫入 DEFAULT_STORAGE_LIMIT（位元組）。",
        "Private server storage limit (MB)": "私有伺服器儲存上限 (MB)",
        "Maps to PRIVATE_SERVER_STORAGE_LIMIT_MB.": "對應 PRIVATE_SERVER_STORAGE_LIMIT_MB。",
        "Donation storage limit (MB)": "贊助方案儲存上限 (MB)",
        "Maps to DONATION_MONTHLY_STORAGE_LIMIT_MB.": "對應 DONATION_MONTHLY_STORAGE_LIMIT_MB。",
        "Upload directory": "上傳目錄",
        "Relative or absolute path used by UPLOAD_DIR.": "UPLOAD_DIR 使用的相對或絕對路徑。",
        "File retention days": "檔案保留天數",
        "Maps to FILE_RETENTION_DAYS.": "對應 FILE_RETENTION_DAYS。",
        "Offline message retention days": "離線訊息保留天數",
        "Maps to OFFLINE_MSG_RETENTION_DAYS.": "對應 OFFLINE_MSG_RETENTION_DAYS。",
        "Register rate limit max": "註冊限流最大次數",
        "Maps to REGISTER_RATE_LIMIT_MAX.": "對應 REGISTER_RATE_LIMIT_MAX。",
        "Register rate limit window (seconds)": "註冊限流視窗（秒）",
        "Maps to REGISTER_RATE_LIMIT_WINDOW.": "對應 REGISTER_RATE_LIMIT_WINDOW。",
        "Registration key": "註冊金鑰",
        "Optional REGISTRATION_KEY for controlled onboarding.": "受控註冊流程使用的可選 REGISTRATION_KEY。",
        "Server identity salt": "伺服器身分 salt",
        "Optional SERVER_IDENTITY_SALT override.": "可選 SERVER_IDENTITY_SALT 覆寫值。",
        "Admin IP allowlist": "管理員 IP 白名單",
        "Comma-separated ADMIN_IP_ALLOWLIST entries.": "以逗號分隔的 ADMIN_IP_ALLOWLIST 項目。",
        "Treat this server as a public hub": "將此伺服器視為公開 Hub",
        "Maps to IS_PUBLIC_HUB.": "對應 IS_PUBLIC_HUB。",
        "PIN sync auto default": "PIN 同步自動預設",
        "Maps to PIN_SYNC_AUTO_DEFAULT.": "對應 PIN_SYNC_AUTO_DEFAULT。",
        "Force manual PIN sync": "強制手動 PIN 同步",
        "Maps to PIN_SYNC_FORCE_MANUAL.": "對應 PIN_SYNC_FORCE_MANUAL。",
        "Push queue workers": "推送佇列工作執行緒",
        "Maps to PUSH_QUEUE_WORKERS.": "對應 PUSH_QUEUE_WORKERS。",
        "Push queue max size": "推送佇列最大容量",
        "Maps to PUSH_QUEUE_MAX_SIZE.": "對應 PUSH_QUEUE_MAX_SIZE。",
        "Presence backend": "Presence 後端",
        "Choose memory or redis.": "選擇 memory 或 redis。",
        "Presence Redis URL": "Presence Redis URL",
        "Required only when presence backend uses redis.": "僅在 presence backend 使用 redis 時需要。",
        "Fanout backend": "Fanout 後端",
        "Fanout Redis URL": "Fanout Redis URL",
        "Required only when fanout backend uses redis.": "僅在 fanout backend 使用 redis 時需要。",
        "Region policy mode": "區域策略模式",
        "Maps to REGION_POLICY_MODE.": "對應 REGION_POLICY_MODE。",
        "Default region": "預設區域",
        "Maps to DEFAULT_REGION.": "對應 DEFAULT_REGION。",
        "China FCM policy": "中國 FCM 策略",
        "Maps to CHINA_FCM_POLICY.": "對應 CHINA_FCM_POLICY。",
        "LEGACY_PUBLIC_URL": "LEGACY_PUBLIC_URL",
        "Optional public base URL used by the Open Legacy Docs button.": "Open Legacy Docs 按鈕使用的可選公開基底 URL。",
        "The backend consists of two FastAPI services:\n• Migration backend (new_main:app) — port 8000\n• Legacy backend (legacy_app) — port 8001\nBoth are automatically started after TOTP verification.": "後端由兩個 FastAPI 服務組成:\n• Migration backend (new_main:app) — port 8000\n• Legacy backend (legacy_app) — port 8001\n兩者都會在 TOTP 驗證後自動啟動。",
        "Open Migration Docs (8000)": "開啟 Migration Docs (8000)",
        "Open Legacy Docs (8001)": "開啟 Legacy Docs (8001)",
        "Enabled": "已啟用",
    },
}


def _enable_high_dpi() -> float:
    scale = 1.0
    if os.name != "nt":
        return scale

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return scale

    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi > 0:
            scale = max(1.0, min(dpi / 96.0, 2.0))
    except Exception:
        scale = 1.0
    return scale


def _normalize_https_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return value.rstrip("/")


def _to_ws_url(http_url: str) -> str:
    value = _normalize_https_url(http_url)
    if value.startswith("https://"):
        return "wss://" + value[len("https://") :]
    if value.startswith("http://"):
        return "ws://" + value[len("http://") :]
    return value


def _extract_host(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""

    if "://" in value:
        parsed = urlparse(value)
        host = (parsed.hostname or "").strip()
        if host:
            return host

    value = value.split("/", 1)[0].strip()
    if value.startswith("[") and "]" in value:
        return value[1:value.index("]")].strip()
    if ":" in value:
        return value.split(":", 1)[0].strip()
    return value


def _safe_int(raw: str, *, default: int, minimum: Optional[int] = None) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _as_bool(raw: Any) -> bool:
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return value
    return f"{value[:4]}...{value[-4:]}"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class SecureTotpStore:
    def __init__(self, tools_dir: Path) -> None:
        self.file_path = tools_dir / ".backend_gui_totp.json"
        self.key_path = tools_dir / ".backend_gui_totp.key"

    def exists(self) -> bool:
        return self.file_path.exists()

    def load(self) -> Optional[dict[str, Any]]:
        if not self.file_path.exists():
            return None
        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        method = payload.get("method", "fernet")
        encrypted = base64.b64decode(payload["payload"])
        if method == "dpapi":
            data = self._dpapi_unprotect(encrypted)
        else:
            data = self._fernet().decrypt(encrypted)
        return json.loads(data.decode("utf-8"))

    def save(self, secret: str, account: str, issuer: str) -> None:
        record = {
            "version": 1,
            "secret": secret,
            "account": account,
            "issuer": issuer,
            "created_at": int(time.time()),
        }
        raw = json.dumps(record, separators=(",", ":")).encode("utf-8")
        method = "dpapi" if os.name == "nt" else "fernet"
        encrypted = self._dpapi_protect(raw) if method == "dpapi" else self._fernet().encrypt(raw)
        wrapper = {
            "version": 1,
            "method": method,
            "payload": base64.b64encode(encrypted).decode("ascii"),
        }
        self.file_path.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")

    def _fernet(self) -> Fernet:
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
        return Fernet(self.key_path.read_bytes().strip())

    def _dpapi_protect(self, data: bytes) -> bytes:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buffer = ctypes.create_string_buffer(data)
        in_blob = _DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)))
        out_blob = _DataBlob()
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "Miraichat Backend GUI TOTP",
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            raise OSError("Unable to protect TOTP configuration with DPAPI")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    def _dpapi_unprotect(self, data: bytes) -> bytes:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_buffer = ctypes.create_string_buffer(data)
        in_blob = _DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)))
        out_blob = _DataBlob()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            raise OSError("Unable to decrypt TOTP configuration")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


class ConfigStore:
    DEFAULTS: dict[str, Any] = {
        "language": "EN",
        "mode": "simple",
        "simple_tunnel_domain": "",
        "simple_storage_mb": str(DEFAULT_STORAGE_MB),
        "backend_host": DEFAULT_HOST,
        "backend_port": str(DEFAULT_PORT),
        "reload": False,
        "log_level": DEFAULT_LOG_LEVEL,
        "access_log": True,
        "is_public_hub": True,
        "public_hub_url": "",
        "api_public_base_url": "",
        "legacy_public_url": "",
        "ws_connection_mode": "tunnel",
        "ws_tunnel_base_url": "",
        "ws_direct_public_url": "",
        "default_storage_limit_mb": str(DEFAULT_STORAGE_MB),
        "private_server_storage_limit_mb": str(DEFAULT_STORAGE_MB),
        "donation_monthly_storage_limit_mb": "200",
        "upload_dir": "stored_files",
        "file_retention_days": "3",
        "offline_msg_retention_days": "7",
        "register_rate_limit_max": "10",
        "register_rate_limit_window": "3600",
        "registration_key": "",
        "server_identity_salt": "",
        "admin_ip_allowlist": "",
        "admin_doc_user": "admin",
        "admin_pass_hash": "",
        "admin_password_set": False,
        "admin_password_updated_at": "",
        "pin_sync_auto_default": True,
        "pin_sync_force_manual": False,
        "presence_backend": "memory",
        "presence_redis_url": "",
        "fanout_backend": "memory",
        "fanout_redis_url": "",
        "region_policy_mode": "auto",
        "default_region": "global",
        "china_fcm_policy": "disable",
        "cloudflare_domain": "",
        "proxy_provider": "cloudflare",
        "proxy_notes": "",
        "push_hub_license": "",
        "push_hub_license_secret": "",
        "push_hub_license_expires_on": "",
        "central_server_token": "",
        "push_hub_server_id": "",
        "push_hub_pem_fingerprint": "",
        "server_notification_type": "Welcome Notification",
        "server_notification_title": "MiraiChat",
        "server_notification_body": WELCOME_NOTIFICATION_BODY,
        "server_notification_image_url": "",
        "server_notification_extra_data": "",
        "server_notification_last_single_payload": "",
    }

    def __init__(self, tools_dir: Path) -> None:
        self.file_path = tools_dir / ".backend_gui_config.json"

    def load(self) -> dict[str, Any]:
        data = dict(self.DEFAULTS)
        if not self.file_path.exists():
            return data
        try:
            incoming = json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception:
            return data
        for key in data:
            if key in incoming:
                data[key] = incoming[key]
        return data

    def save(self, config: dict[str, Any]) -> None:
        payload = {key: config.get(key, self.DEFAULTS[key]) for key in self.DEFAULTS}
        self.file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@dataclass
class LaunchPlan:
    mode: str
    host: str
    port: int
    reload_enabled: bool
    log_level: str
    access_log: bool
    env: dict[str, str]
    public_api_url: str
    public_ws_url: str
    ws_mode: str

    @property
    def local_probe_url(self) -> str:
        host = self.host
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        return f"http://{host}:{self.port}/chat/transport_descriptor"

    @property
    def command(self) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "new_main:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--ws",
            "wsproto",
            "--log-level",
            self.log_level,
        ]
        if self.reload_enabled:
            cmd.append("--reload")
        if not self.access_log:
            cmd.append("--no-access-log")
        return cmd

    @property
    def legacy_command(self) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "chat_backend.legacy_app:app",
            "--host",
            self.host,
            "--port",
            "8001",
            "--ws",
            "wsproto",
            "--log-level",
            self.log_level,
        ]
        if self.reload_enabled:
            cmd.append("--reload")
        if not self.access_log:
            cmd.append("--no-access-log")
        return cmd


class BackendProcessController:
    def __init__(self, project_dir: Path, on_log: callable[[str], None]) -> None:
        self.project_dir = project_dir
        self.on_log = on_log
        self.process: Optional[subprocess.Popen[str]] = None
        self.legacy_process: Optional[subprocess.Popen[str]] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.legacy_reader_thread: Optional[threading.Thread] = None

    def is_running(self) -> bool:
        return (
            (self.process is not None and self.process.poll() is None)
            or (self.legacy_process is not None and self.legacy_process.poll() is None)
        )

    def start(self, plan: LaunchPlan) -> None:
        if self.is_running():
            raise RuntimeError("Backend is already running.")

        env = os.environ.copy()
        env.update(plan.env)
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

        self.on_log("[launcher] Starting backend with new_main:app")
        self.on_log("[launcher] " + " ".join(plan.command))
        self.process = subprocess.Popen(
            plan.command,
            cwd=str(self.project_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
        self.reader_thread = threading.Thread(target=self._pump_output, daemon=True)
        self.reader_thread.start()

        self.on_log("[launcher] Starting legacy admin API with chat_backend.legacy_app:app")
        self.on_log("[launcher] " + " ".join(plan.legacy_command))
        try:
            self.legacy_process = subprocess.Popen(
                plan.legacy_command,
                cwd=str(self.project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=creationflags,
            )
        except Exception:
            self.stop()
            raise
        self.legacy_reader_thread = threading.Thread(target=self._pump_legacy_output, daemon=True)
        self.legacy_reader_thread.start()

    def stop(self) -> None:
        if not self.is_running():
            return
        self._terminate_process("legacy admin API", self.legacy_process)
        self._terminate_process("backend", self.process)

    def _terminate_process(self, label: str, process: Optional[subprocess.Popen[str]]) -> None:
        if process is None or process.poll() is not None:
            return
        self.on_log(f"[launcher] Stopping {label} process")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.on_log(f"[launcher] {label} did not stop in time; killing process")
            process.kill()
            process.wait(timeout=5)

    def _pump_output(self) -> None:
        assert self.process is not None
        stream = self.process.stdout
        if stream is not None:
            for line in stream:
                self.on_log(line.rstrip())
        code = self.process.wait()
        self.on_log(f"[launcher] Backend process exited with code {code}")

    def _pump_legacy_output(self) -> None:
        assert self.legacy_process is not None
        stream = self.legacy_process.stdout
        if stream is not None:
            for line in stream:
                self.on_log(line.rstrip())
        code = self.legacy_process.wait()
        self.on_log(f"[launcher] Legacy admin API process exited with code {code}")


@dataclass
class StatusSnapshot:
    backend_text: str
    backend_color: str
    tunnel_text: str
    tunnel_color: str
    ws_text: str
    ws_color: str
    detail_lines: list[str]


class StatusProbe:
    def probe(self, plan: LaunchPlan, launcher_running: bool) -> StatusSnapshot:
        detail_lines = [f"Local probe: {plan.local_probe_url}"]
        descriptor: Optional[dict[str, Any]] = None

        try:
            response = requests.get(plan.local_probe_url, timeout=2.5)
            if response.ok:
                descriptor = response.json()
        except Exception:
            descriptor = None

        if descriptor:
            backend_text = f"Online on {plan.host}:{plan.port}"
            backend_color = SUCCESS
            detail_lines.append("Transport descriptor reachable")
        elif launcher_running:
            backend_text = "Starting or not yet responding"
            backend_color = WARNING
            detail_lines.append("Process is running but HTTP probe has not responded yet")
        else:
            backend_text = "Stopped"
            backend_color = ERROR
            detail_lines.append("No local backend response detected")

        ws_mode = descriptor.get("ws_connection_mode", plan.ws_mode) if descriptor else plan.ws_mode
        ws_public_url = descriptor.get("ws_public_url", plan.public_ws_url) if descriptor else plan.public_ws_url
        api_public_url = descriptor.get("api_base_url", plan.public_api_url) if descriptor else plan.public_api_url
        if api_public_url:
            detail_lines.append(f"Public API: {api_public_url}")
        if ws_public_url:
            detail_lines.append(f"Public WS: {ws_public_url}")

        if ws_mode == "tunnel":
            tunnel_text = "Tunnel routing enabled"
            tunnel_color = SUCCESS if api_public_url else WARNING
            if api_public_url:
                try:
                    public_response = requests.get(
                        f"{api_public_url}/chat/transport_descriptor",
                        timeout=3.0,
                    )
                    if public_response.ok:
                        tunnel_text = "Tunnel is publicly reachable"
                        tunnel_color = SUCCESS
                        detail_lines.append("Public tunnel descriptor check succeeded")
                    else:
                        tunnel_text = f"Tunnel check returned HTTP {public_response.status_code}"
                        tunnel_color = WARNING
                except Exception:
                    tunnel_text = "Tunnel configured; public reachability check failed"
                    tunnel_color = WARNING
            ws_text = f"tunnel, {ws_public_url or 'pending'}"
            ws_color = SUCCESS if ws_public_url else WARNING
        else:
            tunnel_text = "Tunnel not in use"
            tunnel_color = MUTED
            ws_text = f"direct, {ws_public_url or 'not configured'}"
            ws_color = SUCCESS if ws_public_url else WARNING

        return StatusSnapshot(
            backend_text=backend_text,
            backend_color=backend_color,
            tunnel_text=tunnel_text,
            tunnel_color=tunnel_color,
            ws_text=ws_text,
            ws_color=ws_color,
            detail_lines=detail_lines,
        )


class PushHubCallbackServer:
    def __init__(
        self,
        *,
        on_token: callable,
        log: callable,
    ) -> None:
        self._on_token = on_token
        self._log = log
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.host = "127.0.0.1"
        self.port: Optional[int] = None

    @property
    def callback_url(self) -> str:
        if self.port is None:
            return ""
        return f"http://{self.host}:{self.port}/pushhub"

    def start(self) -> bool:
        if self._server is not None:
            return True

        for candidate in (8765,):
            try:
                self._server = self._create_server(candidate)
                break
            except OSError:
                continue

        if self._server is None:
            try:
                self._server = self._create_server(0)
            except OSError as exc:
                self._log(f"[pushhub] Failed to start callback server: {exc}")
                return False

        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._log(f"[pushhub] Callback server listening at {self.callback_url}")
        return True

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        self.port = None
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass

    def _create_server(self, port: int) -> ThreadingHTTPServer:
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != "/pushhub":
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"Not found")
                    return

                token = parse_qs(parsed.query).get("token", [""])[0].strip()
                if token:
                    owner._log("[pushhub] Received checkout token on local callback")
                    owner._on_token(token)
                    title = "Push Hub License"
                    body = "Payment callback received. You can return to the launcher."
                else:
                    title = "Push Hub License"
                    body = "Missing token in callback URL."

                html = (
                    "<!doctype html><html><head><meta charset='utf-8'/>"
                    "<title>Push Hub License</title></head>"
                    "<body style='font-family:Segoe UI,sans-serif;padding:20px;'>"
                    f"<h2>{title}</h2><p>{body}</p></body></html>"
                ).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, _format: str, *args: Any) -> None:
                return

        return ThreadingHTTPServer((self.host, port), _Handler)


class BackendControlApp(ctk.CTk):
    def __init__(self) -> None:
        self.scale = _enable_high_dpi()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        ctk.set_widget_scaling(self.scale)
        ctk.set_window_scaling(self.scale)

        super().__init__()
        self.title(APP_TITLE)
        self.geometry(f"{int(1280 * self.scale)}x{int(860 * self.scale)}")
        self.minsize(int(1120 * self.scale), int(760 * self.scale))
        self.configure(fg_color=SURFACE)

        if getattr(sys, "frozen", False):
            self.project_dir = Path(sys.executable).resolve().parent
        else:
            self.project_dir = Path(__file__).resolve().parent.parent
        self.tools_dir = self.project_dir / "tools"
        self.config_store = ConfigStore(self.tools_dir)
        self.totp_store = SecureTotpStore(self.tools_dir)
        self.process_controller = BackendProcessController(self.project_dir, self._enqueue_log)
        self.status_probe = StatusProbe()

        self.config = self.config_store.load()
        self.vars = self._build_vars(self.config)
        self.vars["language"].trace_add("write", self._on_language_changed)
        self.vars["api_public_base_url"].trace_add("write", lambda *_args: self._update_architecture_warning())
        self.vars["public_hub_url"].trace_add("write", lambda *_args: self._update_architecture_warning())
        self.vars["legacy_public_url"].trace_add("write", lambda *_args: self._update_architecture_warning())
        self.authenticated = False
        self.pending_setup_secret: Optional[str] = None
        self.pending_setup_account = "admin@miraichat-gui"
        self.pending_setup_issuer = "Miraichat Admin"
        self.qr_image: Optional[ctk.CTkImage] = None
        self.header_logo_image: Optional[ctk.CTkImage] = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.status_thread: Optional[threading.Thread] = None
        self.dashboard_preloaded = False
        self.panel_warmed = False
        self.legacy_status_cache: dict[str, tuple[float, bool]] = {}
        self.legacy_check_thread: Optional[threading.Thread] = None
        self.pushhub_callback_server = PushHubCallbackServer(
            on_token=self._on_pushhub_checkout_token,
            log=self._enqueue_log,
        )

        self.auth_frame: Optional[ctk.CTkFrame] = None
        self.dashboard_frame: Optional[ctk.CTkFrame] = None
        self.header_panel: Optional[ctk.CTkFrame] = None
        self.mode_selector_panel: Optional[ctk.CTkFrame] = None
        self.mode_panel_container: Optional[ctk.CTkFrame] = None
        self.simple_panel: Optional[ctk.CTkFrame] = None
        self.advanced_panel: Optional[ctk.CTkFrame] = None
        self.simple_content_host: Optional[ctk.CTkFrame] = None
        self.simple_static_panel: Optional[ctk.CTkFrame] = None
        self.simple_scroll_panel: Optional[ctk.CTkScrollableFrame] = None
        self.status_panel: Optional[ctk.CTkFrame] = None
        self.logs_panel: Optional[ctk.CTkFrame] = None
        self.mode_title_label: Optional[ctk.CTkLabel] = None
        self.language_label: Optional[ctk.CTkLabel] = None
        self.logs_textbox: Optional[ctk.CTkTextbox] = None
        self.backend_status_value: Optional[ctk.CTkLabel] = None
        self.tunnel_status_value: Optional[ctk.CTkLabel] = None
        self.ws_status_scroll: Optional[ctk.CTkTextbox] = None
        self.status_detail_value: Optional[ctk.CTkTextbox] = None
        self.architecture_warning_label: Optional[ctk.CTkLabel] = None
        self.about_support_modal: Optional[ctk.CTkToplevel] = None
        self.license_expiry_labels: list[ctk.CTkLabel] = []
        self.notification_body_textboxes: list[ctk.CTkTextbox] = []
        self.notification_extra_textboxes: list[ctk.CTkTextbox] = []
        self.simple_layout_fitted = False

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.pushhub_callback_server.start()
        self._build_shell()
        self._show_auth_view()
        self.after(220, self._preload_dashboard_if_locked)
        self.after(150, self._drain_logs)
        self.after(800, self._schedule_status_probe)

    def _build_vars(self, config: dict[str, Any]) -> dict[str, tk.Variable]:
        vars_by_key: dict[str, tk.Variable] = {}
        for key, value in config.items():
            if isinstance(value, bool):
                vars_by_key[key] = tk.BooleanVar(value=value)
            else:
                vars_by_key[key] = tk.StringVar(value=str(value))
        if "language" not in vars_by_key:
            vars_by_key["language"] = tk.StringVar(value="EN")
        if "push_hub_license_secret" not in vars_by_key:
            vars_by_key["push_hub_license_secret"] = tk.StringVar(value="")
        self.totp_code_var = tk.StringVar(value="")
        self.setup_code_var = tk.StringVar(value="")
        self.setup_admin_user_var = tk.StringVar(value=str(config.get("admin_doc_user") or "admin"))
        self.setup_admin_password_var = tk.StringVar(value="")
        self.setup_admin_password_confirm_var = tk.StringVar(value="")
        return vars_by_key

    def _current_language(self) -> str:
        raw = str(self.vars["language"].get() or "EN").strip().upper()
        raw = LANGUAGE_ALIASES.get(raw, raw)
        raw = raw.replace("_", "-")
        return raw if raw in SUPPORTED_LANGUAGES else "EN"

    def _tr(self, key: str) -> str:
        lang = self._current_language()
        return I18N.get(lang, I18N["EN"]).get(key, I18N["EN"].get(key, key))

    def _current_mode(self) -> str:
        raw = str(self.vars["mode"].get() or "simple").strip().lower()
        return raw if raw in {"simple", "advanced"} else "simple"

    def _admin_pass_hash(self) -> str:
        variable = self.vars.get("admin_pass_hash")
        return str(variable.get()).strip() if variable is not None else ""

    def _has_admin_password(self) -> bool:
        return bool(self._admin_pass_hash())

    def _hash_admin_password(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def _store_admin_credentials(self, username: str, password: str) -> None:
        username = (username or "").strip() or "admin"
        password_hash = self._hash_admin_password(password)
        self.vars["admin_doc_user"].set(username)
        self.vars["admin_pass_hash"].set(password_hash)
        self.vars["admin_password_set"].set(True)
        self.vars["admin_password_updated_at"].set(str(int(time.time())))
        self.config_store.save(self._snapshot_config())

    def _on_language_changed(self, *_args: object) -> None:
        normalized = self._current_language()
        if self.vars["language"].get() != normalized:
            self.vars["language"].set(normalized)
            return
        mode_state = self._current_mode()
        if self.vars["mode"].get() != mode_state:
            self.vars["mode"].set(mode_state)
        if self.authenticated and self.dashboard_frame is not None:
            self._refresh_mode_surface()
        else:
            self._show_auth_view()
            self.after(220, self._preload_dashboard_if_locked)

    def _reset_dashboard_cache(self) -> None:
        if self.dashboard_frame is not None:
            self.dashboard_frame.destroy()
        self.dashboard_frame = None
        self.header_panel = None
        self.mode_selector_panel = None
        self.mode_panel_container = None
        self.simple_panel = None
        self.advanced_panel = None
        self.simple_content_host = None
        self.simple_static_panel = None
        self.simple_scroll_panel = None
        self.status_panel = None
        self.logs_panel = None
        self.mode_title_label = None
        self.language_label = None
        self.logs_textbox = None
        self.backend_status_value = None
        self.tunnel_status_value = None
        self.ws_status_scroll = None
        self.status_detail_value = None
        self.architecture_warning_label = None
        self.about_support_modal = None
        self.simple_layout_fitted = False
        self.dashboard_preloaded = False
        self.panel_warmed = False

    def _build_shell(self) -> None:
        container = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        container.pack(fill="both", expand=True, padx=20, pady=20)
        self.shell = container

    def _dashboard_panel_metrics(self) -> dict[str, int]:
        window_width = min(int(1280 * self.scale), int(self.winfo_screenwidth() * 0.95))
        window_height = min(int(860 * self.scale), int(self.winfo_screenheight() * 0.92))
        content_width = max(window_width - 40, int(960 * self.scale))
        split_width = max(content_width - 20, int(920 * self.scale))
        left_width = max(int(split_width * 0.60), int(620 * self.scale))
        right_width = max(split_width - left_width, int(340 * self.scale))
        header_height = int(122 * self.scale)
        workspace_height = max(window_height - header_height - 58, int(540 * self.scale))
        mode_selector_height = int(118 * self.scale)
        status_height = max(int(workspace_height * 0.38), int(280 * self.scale))
        mode_height = max(workspace_height - mode_selector_height - 16, int(360 * self.scale))
        logs_height = max(workspace_height - status_height - 12, int(248 * self.scale))
        return {
            "window_width": window_width,
            "window_height": window_height,
            "header_width": content_width,
            "header_height": header_height,
            "left_width": left_width,
            "right_width": right_width,
            "mode_selector_height": mode_selector_height,
            "mode_height": mode_height,
            "status_height": status_height,
            "logs_height": logs_height,
        }

    def _configure_fixed_panel(self, panel: ctk.CTkFrame, *, width: int, height: int) -> None:
        panel.configure(width=width, height=height)
        panel.grid_propagate(False)

    def _apply_dashboard_layout(self) -> None:
        if self.dashboard_frame is None:
            return
        metrics = self._dashboard_panel_metrics()
        self.geometry(f"{metrics['window_width']}x{metrics['window_height']}")
        self.dashboard_frame.grid_columnconfigure(0, weight=60, minsize=metrics["left_width"])
        self.dashboard_frame.grid_columnconfigure(1, weight=40, minsize=metrics["right_width"])
        self.dashboard_frame.grid_rowconfigure(0, weight=0, minsize=metrics["header_height"])
        if self.header_panel is not None:
            self._configure_fixed_panel(self.header_panel, width=metrics["header_width"], height=metrics["header_height"])
        if self.mode_selector_panel is not None:
            self._configure_fixed_panel(self.mode_selector_panel, width=metrics["left_width"], height=metrics["mode_selector_height"])
        if self.mode_panel_container is not None:
            self.mode_panel_container.configure(width=metrics["left_width"], height=metrics["mode_height"])
            self.mode_panel_container.grid_propagate(False)
        if self.simple_panel is not None:
            self._configure_fixed_panel(self.simple_panel, width=metrics["left_width"], height=metrics["mode_height"])
        if self.advanced_panel is not None:
            self._configure_fixed_panel(self.advanced_panel, width=metrics["left_width"], height=metrics["mode_height"])
        if self.status_panel is not None:
            self._configure_fixed_panel(self.status_panel, width=metrics["right_width"], height=metrics["status_height"])
        if self.logs_panel is not None:
            self._configure_fixed_panel(self.logs_panel, width=metrics["right_width"], height=metrics["logs_height"])

    def _refresh_mode_surface(self) -> None:
        if self.mode_panel_container is None:
            return
        mode_state = self._current_mode()
        if self.mode_title_label is not None:
            self.mode_title_label.configure(text=self._tr("mode_title"))
        if self.language_label is not None:
            self.language_label.configure(text=self._tr("language_label"))
        if self.simple_panel is not None:
            self.simple_panel.destroy()
        if self.advanced_panel is not None:
            self.advanced_panel.destroy()
        self.simple_panel = None
        self.advanced_panel = None
        self.simple_content_host = None
        self.simple_static_panel = None
        self.simple_scroll_panel = None
        self._build_simple_panel(self.mode_panel_container)
        self._build_advanced_panel(self.mode_panel_container)
        self._apply_dashboard_layout()
        self.vars["mode"].set(mode_state)
        self._sync_mode_panels()
        self.after_idle(self._update_simple_scroll_state)

    def _show_auth_view(self) -> None:
        if self.dashboard_frame is not None:
            self.dashboard_frame.pack_forget()
        if self.auth_frame is not None:
            self.auth_frame.destroy()

        frame = ctk.CTkFrame(self.shell, fg_color=SURFACE)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.auth_frame = frame

        card = ctk.CTkFrame(
            frame,
            fg_color=CARD,
            corner_radius=24,
            border_width=1,
            border_color=BORDER,
        )
        card.grid(row=0, column=0, padx=80, pady=60, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)

        eyebrow = ctk.CTkLabel(
            card,
            text="SECURITY GATE",
            text_color=ACCENT,
            font=("Segoe UI Semibold", 13),
        )
        eyebrow.grid(row=0, column=0, columnspan=2, padx=36, pady=(30, 6), sticky="w")

        title = "Verify administrator access"
        subtitle = (
            "Enter a valid TOTP code before any backend controls become available."
            if self.totp_store.exists()
            else "Finish first-run TOTP setup before the launcher unlocks."
        )
        ctk.CTkLabel(
            card,
            text=title,
            text_color=TEXT,
            font=("Segoe UI Semibold", 30),
        ).grid(row=1, column=0, columnspan=2, padx=36, sticky="w")
        ctk.CTkLabel(
            card,
            text=subtitle,
            text_color=MUTED,
            justify="left",
            wraplength=760,
            font=("Segoe UI", 14),
        ).grid(row=2, column=0, columnspan=2, padx=36, pady=(8, 24), sticky="w")

        if self.totp_store.exists():
            self._build_verify_card(card)
        else:
            self._build_setup_card(card)

    def _build_verify_card(self, parent: ctk.CTkFrame) -> None:
        data = self.totp_store.load() or {}
        left = ctk.CTkFrame(parent, fg_color=CARD_ALT, corner_radius=20)
        right = ctk.CTkFrame(parent, fg_color="#ffffff", corner_radius=20, border_width=1, border_color=BORDER)
        left.grid(row=3, column=0, padx=(36, 18), pady=(0, 36), sticky="nsew")
        right.grid(row=3, column=1, padx=(18, 36), pady=(0, 36), sticky="nsew")
        parent.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            left,
            text="Stored authenticator",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w", padx=24, pady=(24, 8))
        ctk.CTkLabel(
            left,
            text=f"Account: {data.get('account', 'admin@miraichat-gui')}\nIssuer: {data.get('issuer', 'Miraichat Admin')}\nSecret: {_mask_secret(data.get('secret', ''))}",
            text_color=MUTED,
            justify="left",
            font=("Segoe UI", 13),
        ).pack(anchor="w", padx=24)
        hint = (
            "Access stays locked until the current 6-digit code is accepted. "
            "The TOTP secret is stored in encrypted JSON under tools/."
        )
        ctk.CTkLabel(
            left,
            text=hint,
            text_color=MUTED,
            justify="left",
            wraplength=360,
            font=("Segoe UI", 13),
        ).pack(anchor="w", padx=24, pady=(18, 24))

        ctk.CTkLabel(
            right,
            text="Current TOTP code",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w", padx=24, pady=(24, 12))
        entry = ctk.CTkEntry(
            right,
            textvariable=self.totp_code_var,
            width=320,
            height=48,
            corner_radius=14,
            font=("Segoe UI", 18),
            placeholder_text="123456",
        )
        entry.pack(anchor="w", padx=24)
        entry.focus_set()
        entry.bind("<Return>", lambda _event: self._verify_existing_totp())

        ctk.CTkButton(
            right,
            text="Unlock dashboard",
            command=self._verify_existing_totp,
            height=44,
            corner_radius=14,
            fg_color=ACCENT,
            hover_color="#215bbd",
            font=("Segoe UI Semibold", 14),
        ).pack(anchor="w", padx=24, pady=(20, 10))

    def _build_setup_card(self, parent: ctk.CTkFrame) -> None:
        if not self.pending_setup_secret:
            self.pending_setup_secret = pyotp.random_base32()
        uri = pyotp.TOTP(self.pending_setup_secret).provisioning_uri(
            name=self.pending_setup_account,
            issuer_name=self.pending_setup_issuer,
        )

        left = ctk.CTkFrame(parent, fg_color="#ffffff", corner_radius=20, border_width=1, border_color=BORDER)
        right = ctk.CTkFrame(parent, fg_color=CARD_ALT, corner_radius=20)
        left.grid(row=3, column=0, padx=(36, 18), pady=(0, 36), sticky="nsew")
        right.grid(row=3, column=1, padx=(18, 36), pady=(0, 36), sticky="nsew")
        parent.grid_rowconfigure(3, weight=1)

        qr = qrcode.QRCode(border=2, box_size=8)
        qr.add_data(uri)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        self.qr_image = ctk.CTkImage(light_image=image, dark_image=image, size=(220, 220))

        ctk.CTkLabel(
            left,
            text="1. Scan this QR code",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w", padx=24, pady=(24, 10))
        ctk.CTkLabel(left, image=self.qr_image, text="").pack(anchor="w", padx=24, pady=(0, 16))

        ctk.CTkLabel(
            left,
            text="Authenticator secret",
            text_color=MUTED,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", padx=24)
        secret_entry = ctk.CTkEntry(
            left,
            width=340,
            height=40,
            corner_radius=12,
            font=("Consolas", 13),
        )
        secret_entry.insert(0, self.pending_setup_secret)
        secret_entry.configure(state="readonly")
        secret_entry.pack(anchor="w", padx=24, pady=(6, 22))

        ctk.CTkLabel(
            right,
            text="2. Confirm with a live code",
            text_color=TEXT,
            font=("Segoe UI Semibold", 18),
        ).pack(anchor="w", padx=24, pady=(24, 12))
        ctk.CTkLabel(
            right,
            text=(
                "This first launch is blocked until the authenticator setup is complete. "
                "Enter the current 6-digit code and create the legacy admin API password."
            ),
            text_color=MUTED,
            justify="left",
            wraplength=360,
            font=("Segoe UI", 13),
        ).pack(anchor="w", padx=24)
        entry = ctk.CTkEntry(
            right,
            textvariable=self.setup_code_var,
            width=320,
            height=48,
            corner_radius=14,
            font=("Segoe UI", 18),
            placeholder_text="123456",
        )
        entry.pack(anchor="w", padx=24, pady=(18, 0))
        entry.bind("<Return>", lambda _event: self._complete_totp_setup())

        ctk.CTkLabel(
            right,
            text="Admin username",
            text_color=MUTED,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", padx=24, pady=(14, 4))
        ctk.CTkEntry(
            right,
            textvariable=self.setup_admin_user_var,
            width=320,
            height=40,
            corner_radius=12,
            placeholder_text="admin",
        ).pack(anchor="w", padx=24)

        ctk.CTkLabel(
            right,
            text="Admin password",
            text_color=MUTED,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", padx=24, pady=(10, 4))
        ctk.CTkEntry(
            right,
            textvariable=self.setup_admin_password_var,
            width=320,
            height=40,
            corner_radius=12,
            show="*",
        ).pack(anchor="w", padx=24)

        ctk.CTkLabel(
            right,
            text="Confirm password",
            text_color=MUTED,
            font=("Segoe UI Semibold", 12),
        ).pack(anchor="w", padx=24, pady=(10, 4))
        ctk.CTkEntry(
            right,
            textvariable=self.setup_admin_password_confirm_var,
            width=320,
            height=40,
            corner_radius=12,
            show="*",
        ).pack(anchor="w", padx=24)

        ctk.CTkButton(
            right,
            text="Verify setup and continue",
            command=self._complete_totp_setup,
            height=44,
            corner_radius=14,
            fg_color=ACCENT,
            hover_color="#215bbd",
            font=("Segoe UI Semibold", 14),
        ).pack(anchor="w", padx=24, pady=(20, 10))

    def _verify_existing_totp(self) -> None:
        code = self.totp_code_var.get().strip()
        try:
            data = self.totp_store.load() or {}
        except Exception as exc:
            messagebox.showerror("TOTP error", f"Unable to read encrypted TOTP file:\n{exc}")
            return
        secret = str(data.get("secret", "")).strip()
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            messagebox.showerror("Verification failed", "The TOTP code is invalid. Access remains locked.")
            return
        self.totp_code_var.set("")
        self.authenticated = True
        if not self._has_admin_password():
            self._open_admin_password_setup_modal(require_totp=True)
            if not self._has_admin_password():
                self.authenticated = False
                return
        self._show_dashboard()

    def _complete_totp_setup(self) -> None:
        if not self.pending_setup_secret:
            messagebox.showerror("TOTP setup", "No pending TOTP secret is available.")
            return
        code = self.setup_code_var.get().strip()
        if not pyotp.TOTP(self.pending_setup_secret).verify(code, valid_window=1):
            messagebox.showerror("Verification failed", "The TOTP code is invalid. Setup is not complete.")
            return
        admin_user = self.setup_admin_user_var.get().strip() or "admin"
        admin_password = self.setup_admin_password_var.get()
        admin_confirm = self.setup_admin_password_confirm_var.get()
        if not admin_password:
            messagebox.showerror("Admin password", "Admin password is required.")
            return
        if admin_password != admin_confirm:
            messagebox.showerror("Admin password", "Admin password confirmation does not match.")
            return
        try:
            self.totp_store.save(
                self.pending_setup_secret,
                self.pending_setup_account,
                self.pending_setup_issuer,
            )
            self._store_admin_credentials(admin_user, admin_password)
        except Exception as exc:
            messagebox.showerror("TOTP setup", f"Unable to save encrypted TOTP configuration:\n{exc}")
            return
        self.setup_code_var.set("")
        self.setup_admin_password_var.set("")
        self.setup_admin_password_confirm_var.set("")
        self.authenticated = True
        self._enqueue_log("[auth] TOTP setup completed; admin password hash stored in GUI config")
        self._show_dashboard()

    def _open_admin_password_setup_modal(self, *, require_totp: bool) -> None:
        top = ctk.CTkToplevel(self)
        top.title("Admin password setup")
        top.geometry("460x520")
        top.configure(fg_color=SURFACE)
        top.transient(self)
        top.grab_set()
        top.attributes("-topmost", True)
        top.grid_columnconfigure(0, weight=1)

        username_var = tk.StringVar(value=str(self.vars["admin_doc_user"].get()).strip() or "admin")
        password_var = tk.StringVar(value="")
        confirm_var = tk.StringVar(value="")
        totp_var = tk.StringVar(value="")

        ctk.CTkLabel(
            top,
            text="Set legacy admin password",
            text_color=TEXT,
            font=("Segoe UI Semibold", 20),
        ).grid(row=0, column=0, padx=22, pady=(22, 6), sticky="w")
        ctk.CTkLabel(
            top,
            text="The password is hashed before saving. Plaintext is used only for this setup form.",
            text_color=MUTED,
            justify="left",
            wraplength=400,
            font=("Segoe UI", 13),
        ).grid(row=1, column=0, padx=22, pady=(0, 14), sticky="w")

        fields = [
            ("Admin username", username_var, None),
            ("Admin password", password_var, "*"),
            ("Confirm password", confirm_var, "*"),
        ]
        row = 2
        for label, variable, show in fields:
            ctk.CTkLabel(top, text=label, text_color=MUTED, font=("Segoe UI Semibold", 12)).grid(row=row, column=0, padx=22, sticky="w")
            row += 1
            entry = ctk.CTkEntry(top, textvariable=variable, height=40, corner_radius=12, show=show or "")
            entry.grid(row=row, column=0, padx=22, pady=(4, 10), sticky="ew")
            row += 1

        if require_totp:
            ctk.CTkLabel(top, text="Current TOTP code", text_color=MUTED, font=("Segoe UI Semibold", 12)).grid(row=row, column=0, padx=22, sticky="w")
            row += 1
            ctk.CTkEntry(top, textvariable=totp_var, height=40, corner_radius=12, placeholder_text="123456").grid(row=row, column=0, padx=22, pady=(4, 10), sticky="ew")
            row += 1

        error_label = ctk.CTkLabel(
            top,
            text="",
            text_color=ERROR,
            justify="left",
            wraplength=400,
            font=("Segoe UI", 12),
        )
        error_label.grid(row=row, column=0, padx=22, pady=(0, 8), sticky="ew")
        row += 1

        def show_error(message: str) -> None:
            error_label.configure(text=message)

        def finish() -> None:
            error_label.configure(text="")
            password = password_var.get()
            if not password:
                show_error("Admin password is required.")
                return
            if password != confirm_var.get():
                show_error("Admin password confirmation does not match.")
                return
            if require_totp:
                try:
                    data = self.totp_store.load() or {}
                    secret = str(data.get("secret", "")).strip()
                    if not pyotp.TOTP(secret).verify(totp_var.get().strip(), valid_window=1):
                        show_error("The TOTP code is invalid.")
                        return
                except Exception as exc:
                    show_error(f"Unable to verify TOTP: {exc}")
                    return
            try:
                self._store_admin_credentials(username_var.get(), password)
            except Exception as exc:
                show_error(f"Unable to save admin password hash: {exc}")
                return
            password_var.set("")
            confirm_var.set("")
            totp_var.set("")
            self._enqueue_log("[auth] Admin password hash stored in GUI config")
            top.destroy()

        footer = ctk.CTkFrame(top, fg_color="transparent")
        footer.grid(row=row, column=0, padx=22, pady=(0, 18), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer,
            text="Save admin password",
            command=finish,
            height=42,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color="#215bbd",
            font=("Segoe UI Semibold", 13),
        ).grid(row=0, column=0, sticky="ew")
        top.bind("<Return>", lambda _event: finish())

        top.wait_window()

    def _show_dashboard(self) -> None:
        if not self.authenticated:
            return
        if self.auth_frame is not None:
            self.auth_frame.destroy()
            self.auth_frame = None
        self._ensure_dashboard_built()
        if self.dashboard_frame is not None and not self.dashboard_frame.winfo_ismapped():
            self.dashboard_frame.pack(fill="both", expand=True)

        if not self.panel_warmed:
            self.after_idle(self._warm_mode_panels)
        self._sync_mode_panels()
        if not self.simple_layout_fitted:
            self.after_idle(self._fit_initial_simple_layout)
        self._enqueue_log("[auth] Dashboard unlocked")

    def _ensure_dashboard_built(self) -> None:
        if self.dashboard_frame is not None:
            return

        frame = ctk.CTkFrame(self.shell, fg_color=SURFACE)
        frame.grid_columnconfigure(0, weight=60)
        frame.grid_columnconfigure(1, weight=40)
        frame.grid_rowconfigure(1, weight=1)
        self.dashboard_frame = frame

        self._build_header(frame)
        self._build_workspace(frame)
        self._apply_dashboard_layout()

    def _preload_dashboard_if_locked(self) -> None:
        if self.authenticated or self.dashboard_preloaded:
            return
        self._ensure_dashboard_built()
        self.dashboard_preloaded = True
        self._enqueue_log("[ui] Dashboard preloaded while waiting for TOTP")

    def _warm_mode_panels(self) -> None:
        if self.panel_warmed:
            return
        if self.simple_panel is None or self.advanced_panel is None:
            return
        # Pre-lay out both panels once to avoid a visible hitch on first mode switch.
        self.simple_panel.grid()
        self.advanced_panel.grid()
        self._sync_mode_panels()
        self.panel_warmed = True

    def _build_header(self, parent: ctk.CTkFrame) -> None:
        header = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=22, border_width=1, border_color=BORDER)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        header.grid_columnconfigure(1, weight=1)
        self.header_panel = header

        logo_path = self.project_dir / BACKEND_LOGO_PATH
        if self.header_logo_image is None and logo_path.exists():
            try:
                logo = Image.open(logo_path).convert("RGBA")
                self.header_logo_image = ctk.CTkImage(light_image=logo, dark_image=logo, size=(64, 64))
            except Exception as exc:
                self._enqueue_log(f"[ui] Unable to load logo {logo_path}: {exc}")

        if self.header_logo_image is not None:
            ctk.CTkLabel(
                header,
                text="",
                image=self.header_logo_image,
            ).grid(row=0, column=0, rowspan=2, padx=(22, 8), pady=16, sticky="w")

        ctk.CTkLabel(
            header,
            text=APP_TITLE,
            text_color=TEXT,
            font=("Segoe UI Semibold", 28),
        ).grid(row=0, column=1, padx=26, pady=(22, 4), sticky="w")
        ctk.CTkButton(
            header,
            text=self._tr("about_support_button"),
            command=self._open_about_support_modal,
            height=36,
            corner_radius=12,
            fg_color=ACCENT_SOFT,
            hover_color="#c5d8ff",
            text_color=ACCENT,
            font=("Segoe UI Semibold", 12),
        ).grid(row=1, column=1, padx=26, pady=(0, 22), sticky="w")

        buttons = ctk.CTkFrame(header, fg_color="transparent")
        buttons.grid(row=0, column=2, rowspan=2, padx=22, pady=18, sticky="e")
        ctk.CTkButton(
            buttons,
            text="Save configuration",
            command=self._save_config,
            height=40,
            corner_radius=14,
            fg_color=ACCENT_SOFT,
            hover_color="#c5d8ff",
            text_color=ACCENT,
            font=("Segoe UI Semibold", 13),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            buttons,
            text="Start backend",
            command=self._start_backend,
            height=40,
            corner_radius=14,
            fg_color=ACCENT,
            hover_color="#215bbd",
            font=("Segoe UI Semibold", 13),
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            buttons,
            text="Stop backend",
            command=self._stop_backend,
            height=40,
            corner_radius=14,
            fg_color="#fbe7e7",
            hover_color="#f3d4d4",
            text_color=ERROR,
            font=("Segoe UI Semibold", 13),
        ).pack(side="left")

    def _build_workspace(self, parent: ctk.CTkFrame) -> None:
        left = ctk.CTkFrame(parent, fg_color="transparent")
        right = ctk.CTkFrame(parent, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        right.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        left.grid_rowconfigure(0, weight=0)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=0)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self._build_mode_selector(left)
        mode_container = ctk.CTkFrame(left, fg_color="transparent")
        mode_container.grid(row=1, column=0, sticky="nsew")
        mode_container.grid_columnconfigure(0, weight=1)
        mode_container.grid_rowconfigure(0, weight=1)
        mode_container.grid_propagate(False)
        self.mode_panel_container = mode_container

        self._build_simple_panel(mode_container)
        self._build_advanced_panel(mode_container)
        self._build_status_panel(right)
        self._build_logs_panel(right)

    def _build_mode_selector(self, parent: ctk.CTkFrame) -> None:
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=22, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        self.mode_selector_panel = frame
        self.mode_title_label = ctk.CTkLabel(
            frame,
            text=self._tr("mode_title"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 20),
        )
        self.mode_title_label.pack(anchor="w", padx=24, pady=(14, 6))

        controls = ctk.CTkFrame(frame, fg_color="transparent")
        controls.pack(fill="x", padx=24, pady=(6, 14))
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=0)

        segmented = ctk.CTkSegmentedButton(
            controls,
            values=["simple", "advanced"],
            variable=self.vars["mode"],
            command=lambda _value: self._sync_mode_panels(),
            height=38,
            corner_radius=14,
            font=("Segoe UI Semibold", 13),
        )
        segmented.grid(row=0, column=0, sticky="w")

        language_box = ctk.CTkFrame(controls, fg_color="transparent")
        language_box.grid(row=0, column=1, sticky="e")
        self.language_label = ctk.CTkLabel(
            language_box,
            text=self._tr("language_label"),
            text_color=MUTED,
            font=("Segoe UI Semibold", 12),
        )
        self.language_label.pack(anchor="e")
        ctk.CTkOptionMenu(
            language_box,
            variable=self.vars["language"],
            values=SUPPORTED_LANGUAGES,
            height=36,
            width=110,
            corner_radius=12,
        ).pack(anchor="e", pady=(4, 0))

    def _build_simple_panel(self, parent: ctk.CTkFrame) -> None:
        frame = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=22, border_width=1, border_color=BORDER)
        frame.grid(row=0, column=0, sticky="nsew")
        self.simple_panel = frame
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)
        self._apply_dashboard_layout()

        ctk.CTkLabel(
            frame,
            text=self._ui_text("Simple mode"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 22),
        ).grid(row=0, column=0, padx=24, pady=(22, 6), sticky="w")
        ctk.CTkLabel(
            frame,
            text=self._ui_text("HTTPS and WebSocket are routed through the same Cloudflare Tunnel domain."),
            text_color=MUTED,
            wraplength=640,
            justify="left",
            font=("Segoe UI", 13),
        ).grid(row=1, column=0, padx=24, sticky="w")

        content_host = ctk.CTkFrame(frame, fg_color="transparent")
        content_host.grid(row=2, column=0, padx=12, pady=(8, 18), sticky="nsew")
        content_host.grid_columnconfigure(0, weight=1)
        content_host.grid_rowconfigure(0, weight=1)
        self.simple_content_host = content_host

        scroll = ctk.CTkScrollableFrame(content_host, fg_color=CARD, corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        self.simple_static_panel = None
        self.simple_scroll_panel = scroll

        self._build_simple_content(scroll)
        frame.bind("<Configure>", lambda _event: self.after_idle(self._update_simple_scroll_state))

    def _build_simple_content(self, parent: ctk.CTkBaseClass) -> None:
        warning = ctk.CTkFrame(parent, fg_color="#fff2dd", corner_radius=16, border_width=1, border_color="#f0d3a3")
        warning.grid(row=0, column=0, padx=24, pady=(6, 18), sticky="ew")
        ctk.CTkLabel(
            warning,
            text=self._ui_text("Simple mode is recommended for deployments with fewer than 10 users.\nAt larger scale, tunneled WebSocket stability can decrease."),
            text_color=WARNING,
            justify="left",
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="w", padx=18, pady=14)

        self._field_block(
            parent,
            row=1,
            label="Cloudflare Tunnel domain",
            description="Example: https://chat.example.miraichat.net",
            variable=self.vars["simple_tunnel_domain"],
            placeholder="https://your-domain.example",
        )
        self._field_block(
            parent,
            row=2,
            label="Per-user storage size (MB)",
            description="Applied to DEFAULT_STORAGE_LIMIT and private-server storage defaults.",
            variable=self.vars["simple_storage_mb"],
            placeholder="100",
        )
        self._push_hub_license_block(parent, row=3)
        self._server_notification_block(parent, row=4)
        ctk.CTkFrame(parent, fg_color="transparent", height=1).grid(row=5, column=0, padx=24, pady=(0, 8), sticky="ew")

    def _build_advanced_panel(self, parent: ctk.CTkFrame) -> None:
        outer = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=22, border_width=1, border_color=BORDER)
        outer.grid(row=0, column=0, sticky="nsew")
        self.advanced_panel = outer
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(2, weight=1)
        self._apply_dashboard_layout()

        ctk.CTkLabel(
            outer,
            text=self._ui_text("Advanced mode"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 22),
        ).grid(row=0, column=0, padx=24, pady=(22, 6), sticky="w")
        ctk.CTkLabel(
            outer,
            text=self._ui_text("Expose HTTPS and WebSocket separately, and tune runtime and backend environment mappings in detail."),
            text_color=MUTED,
            justify="left",
            wraplength=620,
            font=("Segoe UI", 13),
        ).grid(row=1, column=0, padx=24, pady=(0, 4), sticky="w")

        scroll = ctk.CTkScrollableFrame(outer, fg_color=CARD, corner_radius=0)
        scroll.grid(row=2, column=0, padx=12, pady=(8, 18), sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        self._section_label(scroll, 0, self._ui_text("Public transport"))
        self._field_block(scroll, 1, "Public hub URL", "Used by federation and client metadata.", self.vars["public_hub_url"], "https://chat.example.com")
        self._field_block(scroll, 2, "API public base URL", "HTTPS endpoint advertised to clients.", self.vars["api_public_base_url"], "https://api.example.com")
        self._option_block(scroll, 3, "WebSocket connection mode", "Choose tunnel for proxied WS or direct for a dedicated public WS endpoint.", self.vars["ws_connection_mode"], ["tunnel", "direct"])
        self._field_block(scroll, 4, "WS tunnel base URL", "Used when WS_CONNECTION_MODE=tunnel.", self.vars["ws_tunnel_base_url"], "wss://api.example.com")
        self._field_block(scroll, 5, "WS direct public URL", "Used when WS_CONNECTION_MODE=direct.", self.vars["ws_direct_public_url"], "wss://ws.example.com")
        self._option_block(scroll, 6, "Reverse proxy profile", "Saved for operator clarity; does not alter backend code.", self.vars["proxy_provider"], ["cloudflare", "nginx", "other"])
        self._field_block(scroll, 7, "Cloudflare Tunnel domain", "Optional reference field for operators using Cloudflare Tunnel.", self.vars["cloudflare_domain"], "https://chat.example.com")

        self._section_label(scroll, 8, self._ui_text("Runtime"))
        self._field_block(scroll, 9, "Backend host", "Host passed to uvicorn.", self.vars["backend_host"], DEFAULT_HOST)
        self._field_block(scroll, 10, "Backend port", "Port passed to uvicorn.", self.vars["backend_port"], str(DEFAULT_PORT))
        self._toggle_block(scroll, 11, "Reload code on change", "Maps to uvicorn --reload.", self.vars["reload"])
        self._option_block(scroll, 12, "Log level", "Uvicorn log level.", self.vars["log_level"], ["critical", "error", "warning", "info", "debug", "trace"])
        self._toggle_block(scroll, 13, "Access log", "Disable if you want quieter terminal output.", self.vars["access_log"])

        self._section_label(scroll, 14, self._ui_text("Storage and quotas"))
        self._field_block(scroll, 15, "Default storage limit (MB)", "Writes DEFAULT_STORAGE_LIMIT in bytes for new users.", self.vars["default_storage_limit_mb"], "100")
        self._field_block(scroll, 16, "Private server storage limit (MB)", "Maps to PRIVATE_SERVER_STORAGE_LIMIT_MB.", self.vars["private_server_storage_limit_mb"], "100")
        self._field_block(scroll, 17, "Donation storage limit (MB)", "Maps to DONATION_MONTHLY_STORAGE_LIMIT_MB.", self.vars["donation_monthly_storage_limit_mb"], "200")
        self._field_block(scroll, 18, "Upload directory", "Relative or absolute path used by UPLOAD_DIR.", self.vars["upload_dir"], "stored_files")
        self._field_block(scroll, 19, "File retention days", "Maps to FILE_RETENTION_DAYS.", self.vars["file_retention_days"], "3")
        self._field_block(scroll, 20, "Offline message retention days", "Maps to OFFLINE_MSG_RETENTION_DAYS.", self.vars["offline_msg_retention_days"], "7")

        self._section_label(scroll, 21, self._ui_text("Rate limiting and security"))
        self._field_block(scroll, 22, "Register rate limit max", "Maps to REGISTER_RATE_LIMIT_MAX.", self.vars["register_rate_limit_max"], "10")
        self._field_block(scroll, 23, "Register rate limit window (seconds)", "Maps to REGISTER_RATE_LIMIT_WINDOW.", self.vars["register_rate_limit_window"], "3600")
        self._field_block(scroll, 24, "Registration key", "Optional REGISTRATION_KEY for controlled onboarding.", self.vars["registration_key"], "")
        self._field_block(scroll, 25, "Server identity salt", "Optional SERVER_IDENTITY_SALT override.", self.vars["server_identity_salt"], "")
        self._field_block(scroll, 26, "Admin IP allowlist", "Comma-separated ADMIN_IP_ALLOWLIST entries.", self.vars["admin_ip_allowlist"], "203.0.113.10,203.0.113.11")
        self._toggle_block(scroll, 27, "Treat this server as a public hub", "Maps to IS_PUBLIC_HUB.", self.vars["is_public_hub"])
        self._toggle_block(scroll, 28, "PIN sync auto default", "Maps to PIN_SYNC_AUTO_DEFAULT.", self.vars["pin_sync_auto_default"])
        self._toggle_block(scroll, 29, "Force manual PIN sync", "Maps to PIN_SYNC_FORCE_MANUAL.", self.vars["pin_sync_force_manual"])

        self._section_label(scroll, 30, self._ui_text("Performance"))
        self._option_block(scroll, 31, "Presence backend", "Choose memory or redis.", self.vars["presence_backend"], ["memory", "redis"])
        self._field_block(scroll, 32, "Presence Redis URL", "Required only when presence backend uses redis.", self.vars["presence_redis_url"], "redis://127.0.0.1:6379/0")
        self._option_block(scroll, 33, "Fanout backend", "Choose memory or redis.", self.vars["fanout_backend"], ["memory", "redis"])
        self._field_block(scroll, 34, "Fanout Redis URL", "Required only when fanout backend uses redis.", self.vars["fanout_redis_url"], "redis://127.0.0.1:6379/1")
        self._option_block(scroll, 35, "Region policy mode", "Maps to REGION_POLICY_MODE.", self.vars["region_policy_mode"], ["auto", "manual"])
        self._option_block(scroll, 36, "Default region", "Maps to DEFAULT_REGION.", self.vars["default_region"], ["global", "china", "unknown"])
        self._option_block(scroll, 37, "China FCM policy", "Maps to CHINA_FCM_POLICY.", self.vars["china_fcm_policy"], ["disable", "best_effort"])
        self._push_hub_license_block(scroll, row=38)
        self._server_notification_block(scroll, row=39)
        self._section_label(scroll, 41, self._ui_text("Legacy service access"))
        self._field_block(scroll, 42, "LEGACY_PUBLIC_URL", "Optional public base URL used by the Open Legacy Docs button.", self.vars["legacy_public_url"], "https://legacy.example.com")

        self._section_label(scroll, 43, self._ui_text("Backend architecture (ports 8000 / 8001)"))
        architecture_box = ctk.CTkFrame(scroll, fg_color=CARD_ALT, corner_radius=14)
        architecture_box.grid(row=44, column=0, padx=24, pady=(0, 16), sticky="ew")
        architecture_box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            architecture_box,
            text=self._ui_text(
                "The backend consists of two FastAPI services:\n"
                "- Migration backend (new_main:app) - port 8000\n"
                "- Legacy backend (legacy_app) - port 8001\n"
                "Both are automatically started after TOTP verification."
            ),
            text_color=TEXT,
            justify="left",
            font=("Segoe UI", 13),
        ).grid(row=0, column=0, padx=16, pady=(14, 10), sticky="w")

        docs_button_row = ctk.CTkFrame(architecture_box, fg_color="transparent")
        docs_button_row.grid(row=1, column=0, padx=16, pady=(0, 10), sticky="w")

        ctk.CTkButton(
            docs_button_row,
            text=self._ui_text("Open Migration Docs (8000)"),
            command=self._open_migration_docs_8000,
            height=36,
            corner_radius=12,
            fg_color=ACCENT_SOFT,
            hover_color="#c5d8ff",
            text_color=ACCENT,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            docs_button_row,
            text=self._ui_text("Open Legacy Docs (8001)"),
            command=self._open_legacy_docs_8001,
            height=36,
            corner_radius=12,
            fg_color="#e8f7ef",
            hover_color="#d5f1e3",
            text_color=SUCCESS,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")

        self.architecture_warning_label = ctk.CTkLabel(
            architecture_box,
            text="",
            text_color=WARNING,
            justify="left",
            font=("Segoe UI Semibold", 12),
        )
        self.architecture_warning_label.grid(row=2, column=0, padx=16, pady=(0, 14), sticky="w")
        self._update_architecture_warning()

    def _build_status_panel(self, parent: ctk.CTkFrame) -> None:
        panel = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=22, border_width=1, border_color=BORDER)
        panel.grid(row=0, column=0, sticky="new", pady=(0, 12))
        panel.grid_columnconfigure(0, weight=1)
        self.status_panel = panel
        self._apply_dashboard_layout()

        ctk.CTkLabel(
            panel,
            text=self._tr("status_title"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 22),
        ).grid(row=0, column=0, padx=24, pady=(16, 6), sticky="w")
        ctk.CTkLabel(
            panel,
            text=self._tr("status_desc"),
            text_color=MUTED,
            justify="left",
            wraplength=420,
            font=("Segoe UI", 13),
        ).grid(row=1, column=0, padx=24, sticky="w")

        self.backend_status_value = self._status_chip(panel, 2, "Backend")
        self.tunnel_status_value = self._status_chip(panel, 3, "Tunnel")

        self.ws_status_scroll = ctk.CTkTextbox(
            panel,
            height=36,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
            fg_color=CARD_ALT,
            text_color=MUTED,
            font=("Segoe UI Semibold", 15),
            wrap="none",
        )
        self.ws_status_scroll.grid(row=5, column=0, padx=24, pady=(10, 0), sticky="ew")
        self.ws_status_scroll.insert("1.0", "WebSocket: Waiting for probe")
        self.ws_status_scroll.configure(state="disabled")

        ctk.CTkLabel(
            panel,
            text=self._tr("local_probe_title"),
            text_color=MUTED,
            font=("Segoe UI Semibold", 12),
        ).grid(row=6, column=0, padx=24, pady=(14, 4), sticky="w")

        self.status_detail_value = ctk.CTkTextbox(
            panel,
            height=108,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
            fg_color=CARD_ALT,
            text_color=TEXT,
            font=("Consolas", 12),
            wrap="word",
        )
        self.status_detail_value.grid(row=7, column=0, padx=24, pady=(0, 14), sticky="ew")
        self.status_detail_value.insert("1.0", self._tr("local_probe_empty"))
        self.status_detail_value.configure(state="disabled")

    def _build_logs_panel(self, parent: ctk.CTkFrame) -> None:
        panel = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=22, border_width=1, border_color=BORDER)
        panel.grid(row=1, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        self.logs_panel = panel
        self._apply_dashboard_layout()

        ctk.CTkLabel(
            panel,
            text=self._tr("logs_title"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 22),
        ).grid(row=0, column=0, padx=24, pady=(22, 12), sticky="w")
        self.logs_textbox = ctk.CTkTextbox(
            panel,
            corner_radius=16,
            border_width=1,
            border_color=BORDER,
            fg_color="#0f1b2b",
            text_color="#d8e6ff",
            font=("Consolas", 12),
        )
        self.logs_textbox.grid(row=1, column=0, padx=24, pady=(0, 24), sticky="nsew")
        self.logs_textbox.insert("1.0", "Launcher ready.\n")
        self.logs_textbox.configure(state="disabled")

    def _field_block(
        self,
        parent: ctk.CTkBaseClass,
        row: int,
        label: str,
        description: str,
        variable: tk.Variable,
        placeholder: str,
    ) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, padx=24, pady=(0, 16), sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=self._ui_text(label), text_color=TEXT, font=("Segoe UI Semibold", 15)).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(box, text=self._ui_text(description), text_color=MUTED, font=("Segoe UI", 12), justify="left", wraplength=760).grid(row=1, column=0, pady=(2, 8), sticky="w")
        entry = ctk.CTkEntry(box, textvariable=variable, placeholder_text=placeholder, height=40, corner_radius=12)
        entry.grid(row=2, column=0, sticky="ew")

    def _option_block(
        self,
        parent: ctk.CTkBaseClass,
        row: int,
        label: str,
        description: str,
        variable: tk.Variable,
        values: list[str],
    ) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, padx=24, pady=(0, 16), sticky="ew")
        ctk.CTkLabel(box, text=self._ui_text(label), text_color=TEXT, font=("Segoe UI Semibold", 15)).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(box, text=self._ui_text(description), text_color=MUTED, font=("Segoe UI", 12), justify="left", wraplength=760).grid(row=1, column=0, pady=(2, 8), sticky="w")
        ctk.CTkOptionMenu(box, variable=variable, values=values, height=40, corner_radius=12).grid(row=2, column=0, sticky="w")

    def _toggle_block(
        self,
        parent: ctk.CTkBaseClass,
        row: int,
        label: str,
        description: str,
        variable: tk.Variable,
    ) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, padx=24, pady=(0, 16), sticky="ew")
        ctk.CTkLabel(box, text=self._ui_text(label), text_color=TEXT, font=("Segoe UI Semibold", 15)).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(box, text=self._ui_text(description), text_color=MUTED, font=("Segoe UI", 12), justify="left", wraplength=760).grid(row=1, column=0, pady=(2, 8), sticky="w")
        ctk.CTkSwitch(box, text=self._ui_text("Enabled"), variable=variable, onvalue=True, offvalue=False).grid(row=2, column=0, sticky="w")

    def _section_label(self, parent: ctk.CTkBaseClass, row: int, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=self._ui_text(text),
            text_color=ACCENT,
            font=("Segoe UI Semibold", 18),
        ).grid(row=row, column=0, padx=24, pady=(14, 14), sticky="w")

    def _push_hub_license_block(self, parent: ctk.CTkBaseClass, row: int) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, padx=24, pady=(0, 16), sticky="ew")
        box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            box,
            text=self._ui_text("Push Hub License"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 15),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            box,
            text=self._ui_text("Purchase from donate.miraichat.net, then confirm to validate and persist this server license."),
            text_color=MUTED,
            font=("Segoe UI", 12),
            justify="left",
            wraplength=760,
        ).grid(row=1, column=0, pady=(2, 8), sticky="w")

        ctk.CTkLabel(
            box,
            text=self._ui_text("Push Hub License Key"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 12),
        ).grid(row=2, column=0, sticky="w")
        ctk.CTkEntry(
            box,
            textvariable=self.vars["push_hub_license"],
            placeholder_text=self._ui_text("Enter Push Hub License Key"),
            height=40,
            corner_radius=12,
        ).grid(row=3, column=0, pady=(4, 0), sticky="ew")

        ctk.CTkLabel(
            box,
            text=self._ui_text("Push Hub License Secret"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 12),
        ).grid(row=4, column=0, pady=(8, 0), sticky="w")
        ctk.CTkEntry(
            box,
            textvariable=self.vars["push_hub_license_secret"],
            placeholder_text=self._ui_text("Enter Push Hub License Secret"),
            show="*",
            height=40,
            corner_radius=12,
        ).grid(row=5, column=0, pady=(4, 0), sticky="ew")

        expiry_text = self._license_expiry_text()
        license_expiry_label = ctk.CTkLabel(
            box,
            text=self._ui_text(expiry_text),
            text_color=MUTED,
            font=("Segoe UI", 12),
        )
        license_expiry_label.grid(row=6, column=0, pady=(6, 0), sticky="w")
        self.license_expiry_labels.append(license_expiry_label)

        buttons = ctk.CTkFrame(box, fg_color="transparent")
        buttons.grid(row=7, column=0, pady=(10, 0), sticky="w")

        ctk.CTkButton(
            buttons,
            text=self._ui_text("Purchase Push Hub License"),
            command=self._purchase_push_hub_license,
            height=36,
            corner_radius=12,
            fg_color=ACCENT_SOFT,
            hover_color="#c5d8ff",
            text_color=ACCENT,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkButton(
            buttons,
            text=self._ui_text("Confirm License"),
            command=self._confirm_push_hub_license,
            height=36,
            corner_radius=12,
            fg_color="#e8f7ef",
            hover_color="#d5f1e3",
            text_color=SUCCESS,
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")

    def _server_notification_block(self, parent: ctk.CTkBaseClass, row: int) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, padx=24, pady=(0, 16), sticky="ew")
        box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            box,
            text=self._ui_text("Server Notification"),
            text_color=TEXT,
            font=("Segoe UI Semibold", 15),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkOptionMenu(
            box,
            variable=self.vars["server_notification_type"],
            values=["Welcome Notification", "Single Notification"],
            command=lambda _value: self._load_notification_template(),
            height=40,
            corner_radius=12,
        ).grid(row=1, column=0, pady=(8, 10), sticky="w")

        self._notification_entry(box, 2, "title", self.vars["server_notification_title"], "MiraiChat")
        self._notification_textbox(
            box,
            4,
            "body",
            str(self.vars["server_notification_body"].get()),
            self.vars["server_notification_body"],
            self.notification_body_textboxes,
            height=92,
        )
        self._notification_entry(box, 6, "image_url (optional)", self.vars["server_notification_image_url"], "https://example.com/image.png")
        self._notification_textbox(
            box,
            8,
            "extra_data (optional JSON)",
            str(self.vars["server_notification_extra_data"].get()),
            self.vars["server_notification_extra_data"],
            self.notification_extra_textboxes,
            height=72,
        )

        ctk.CTkButton(
            box,
            text=self._ui_text("Send Notification"),
            command=self._send_server_notification,
            height=36,
            corner_radius=12,
            fg_color="#e8f7ef",
            hover_color="#d5f1e3",
            text_color=SUCCESS,
            font=("Segoe UI Semibold", 12),
        ).grid(row=10, column=0, pady=(10, 0), sticky="w")

    def _notification_entry(
        self,
        parent: ctk.CTkBaseClass,
        row: int,
        label: str,
        variable: tk.Variable,
        placeholder: str,
    ) -> None:
        ctk.CTkLabel(parent, text=self._ui_text(label), text_color=TEXT, font=("Segoe UI Semibold", 12)).grid(row=row, column=0, sticky="w")
        ctk.CTkEntry(
            parent,
            textvariable=variable,
            placeholder_text=self._ui_text(placeholder),
            height=40,
            corner_radius=12,
        ).grid(row=row + 1, column=0, pady=(4, 8), sticky="ew")

    def _notification_textbox(
        self,
        parent: ctk.CTkBaseClass,
        row: int,
        label: str,
        value: str,
        variable: tk.Variable,
        registry: list[ctk.CTkTextbox],
        *,
        height: int,
    ) -> ctk.CTkTextbox:
        ctk.CTkLabel(parent, text=self._ui_text(label), text_color=TEXT, font=("Segoe UI Semibold", 12)).grid(row=row, column=0, sticky="w")
        textbox = ctk.CTkTextbox(parent, height=height, corner_radius=12, border_width=1, border_color=BORDER)
        textbox.grid(row=row + 1, column=0, pady=(4, 8), sticky="ew")
        textbox.insert("1.0", value)
        textbox.bind("<KeyRelease>", lambda _event, tb=textbox, var=variable: var.set(tb.get("1.0", "end-1c")))
        registry.append(textbox)
        return textbox

    def _status_chip(self, parent: ctk.CTkFrame, row: int, title: str) -> ctk.CTkLabel:
        box = ctk.CTkFrame(parent, fg_color=CARD_ALT, corner_radius=16)
        box.grid(row=row, column=0, padx=24, pady=(14 if row == 2 else 10, 0), sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        value = ctk.CTkLabel(box, text=f"{title}: Waiting for probe", text_color=MUTED, font=("Segoe UI Semibold", 15))
        value.grid(row=0, column=0, padx=16, pady=12, sticky="w")
        return value

    def _ui_text(self, text: str) -> str:
        lang = self._current_language()
        if lang == "EN":
            return text
        translations = {
            "JP": {
                "Simple mode": "\u7c21\u5358\u30e2\u30fc\u30c9",
                "HTTPS and WebSocket are routed through the same Cloudflare Tunnel domain.": "HTTPS \u3068 WebSocket \u306f\u540c\u3058 Cloudflare Tunnel \u30c9\u30e1\u30a4\u30f3\u3092\u7d4c\u7531\u3057\u307e\u3059\u3002",
                "Simple mode is recommended for deployments with fewer than 10 users.\nAt larger scale, tunneled WebSocket stability can decrease.": "\u7c21\u5358\u30e2\u30fc\u30c9\u306f 10 \u4eba\u672a\u6e80\u306e\u5c0f\u898f\u6a21\u904b\u7528\u306b\u63a8\u5968\u3055\u308c\u307e\u3059\u3002\n\u5927\u898f\u6a21\u3067\u306f\u30c8\u30f3\u30cd\u30eb\u7d4c\u7531 WebSocket \u306e\u5b89\u5b9a\u6027\u304c\u4f4e\u4e0b\u3059\u308b\u5834\u5408\u304c\u3042\u308a\u307e\u3059\u3002",
                "Advanced mode": "\u9032\u968e\u30e2\u30fc\u30c9",
                "Expose HTTPS and WebSocket separately, and tune runtime and backend environment mappings in detail.": "HTTPS \u3068 WebSocket \u3092\u5206\u96e2\u516c\u958b\u3057\u3001\u5b9f\u884c\u8a2d\u5b9a\u3068\u30d0\u30c3\u30af\u30a8\u30f3\u30c9\u74b0\u5883\u5909\u6570\u30de\u30c3\u30d4\u30f3\u30b0\u3092\u8a73\u7d30\u306b\u8abf\u6574\u3057\u307e\u3059\u3002",
                "Public transport": "\u516c\u958b\u30c8\u30e9\u30f3\u30b9\u30dd\u30fc\u30c8",
                "Runtime": "\u5b9f\u884c\u6642\u8a2d\u5b9a",
                "Storage and quotas": "\u30b9\u30c8\u30ec\u30fc\u30b8\u3068\u30af\u30a9\u30fc\u30bf",
                "Rate limiting and security": "\u30ec\u30fc\u30c8\u5236\u9650\u3068\u30bb\u30ad\u30e5\u30ea\u30c6\u30a3",
                "Performance": "\u30d1\u30d5\u30a9\u30fc\u30de\u30f3\u30b9",
                "Legacy service access": "\u65e7\u7248\u30b5\u30fc\u30d3\u30b9\u30a2\u30af\u30bb\u30b9",
                "Backend architecture (ports 8000 / 8001)": "\u30d0\u30c3\u30af\u30a8\u30f3\u30c9\u69cb\u6210 (8000 / 8001)",
                "Cloudflare Tunnel domain": "Cloudflare Tunnel \u30c9\u30e1\u30a4\u30f3",
                "Per-user storage size (MB)": "\u30e6\u30fc\u30b6\u30fc\u3054\u3068\u306e\u30b9\u30c8\u30ec\u30fc\u30b8\u5bb9\u91cf (MB)",
                "Public hub URL": "\u516c\u958b hub URL",
                "API public base URL": "API \u516c\u958b\u30d9\u30fc\u30b9 URL",
                "WebSocket connection mode": "WebSocket \u63a5\u7d9a\u30e2\u30fc\u30c9",
                "WS tunnel base URL": "WS \u30c8\u30f3\u30cd\u30eb\u30d9\u30fc\u30b9 URL",
                "WS direct public URL": "WS \u76f4\u63a5\u516c\u958b URL",
                "Reverse proxy profile": "\u30ea\u30d0\u30fc\u30b9\u30d7\u30ed\u30ad\u30b7\u30d7\u30ed\u30d5\u30a1\u30a4\u30eb",
                "Backend host": "\u30d0\u30c3\u30af\u30a8\u30f3\u30c9\u30db\u30b9\u30c8",
                "Backend port": "\u30d0\u30c3\u30af\u30a8\u30f3\u30c9\u30dd\u30fc\u30c8",
                "Push Hub License": "Push Hub License",
                "Enter Push Hub License Key": "Push Hub License Key \u3092\u5165\u529b",
                "Purchase Push Hub License": "Push Hub License \u3092\u8cfc\u5165",
                "Confirm License": "\u30e9\u30a4\u30bb\u30f3\u30b9\u78ba\u8a8d",
                "Open Migration Docs (8000)": "Migration Docs \u3092\u958b\u304f (8000)",
                "Open Legacy Docs (8001)": "Legacy Docs \u3092\u958b\u304f (8001)",
                "Set LEGACY_PUBLIC_URL in Advanced Mode to enable 8001 reachability checks.": "Advanced Mode \u3067 LEGACY_PUBLIC_URL \u3092\u8a2d\u5b9a\u3059\u308b\u3068 8001 \u5230\u9054\u78ba\u8a8d\u3092\u6709\u52b9\u5316\u3067\u304d\u307e\u3059\u3002",
                "Legacy backend (8001) is not reachable.\nMake sure totp.py has started it and Cloudflare Tunnel is configured.": "Legacy backend (8001) \u306b\u5230\u9054\u3067\u304d\u307e\u305b\u3093\u3002\ntotp.py \u306b\u3088\u308b\u8d77\u52d5\u3068 Cloudflare Tunnel \u8a2d\u5b9a\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002",
                "Checking legacy backend reachability...": "legacy backend \u306e\u5230\u9054\u6027\u3092\u78ba\u8a8d\u4e2d...",
                "Enabled": "\u6709\u52b9",
            },
            "ZH-CN": {
                "Simple mode": "\u7b80\u5355\u6a21\u5f0f",
                "HTTPS and WebSocket are routed through the same Cloudflare Tunnel domain.": "HTTPS \u4e0e WebSocket \u901a\u8fc7\u540c\u4e00\u4e2a Cloudflare Tunnel \u57df\u540d\u8f6c\u53d1\u3002",
                "Simple mode is recommended for deployments with fewer than 10 users.\nAt larger scale, tunneled WebSocket stability can decrease.": "\u7b80\u5355\u6a21\u5f0f\u5efa\u8bae\u7528\u4e8e\u5c11\u4e8e 10 \u4eba\u7684\u90e8\u7f72\u3002\n\u89c4\u6a21\u53d8\u5927\u65f6\uff0c\u9694\u9053 WebSocket \u7a33\u5b9a\u6027\u53ef\u80fd\u4e0b\u964d\u3002",
                "Advanced mode": "\u9ad8\u7ea7\u6a21\u5f0f",
                "Expose HTTPS and WebSocket separately, and tune runtime and backend environment mappings in detail.": "\u5206\u79bb\u516c\u5f00 HTTPS \u4e0e WebSocket\uff0c\u5e76\u53ef\u8be6\u7ec6\u8c03\u6574\u8fd0\u884c\u53c2\u6570\u4e0e\u540e\u7aef\u73af\u5883\u53d8\u91cf\u6620\u5c04\u3002",
                "Public transport": "\u516c\u5f00\u4f20\u8f93",
                "Runtime": "\u8fd0\u884c\u65f6",
                "Storage and quotas": "\u5b58\u50a8\u4e0e\u914d\u989d",
                "Rate limiting and security": "\u9650\u6d41\u4e0e\u5b89\u5168",
                "Performance": "\u6027\u80fd",
                "Legacy service access": "\u65e7\u7248\u670d\u52a1\u8bbf\u95ee",
                "Backend architecture (ports 8000 / 8001)": "\u540e\u7aef\u67b6\u6784 (8000 / 8001)",
                "Cloudflare Tunnel domain": "Cloudflare Tunnel \u57df\u540d",
                "Per-user storage size (MB)": "\u6bcf\u7528\u6237\u5b58\u50a8\u5927\u5c0f (MB)",
                "Public hub URL": "\u516c\u5f00 hub URL",
                "API public base URL": "API \u516c\u5f00\u57fa\u7840 URL",
                "WebSocket connection mode": "WebSocket \u8fde\u63a5\u6a21\u5f0f",
                "WS tunnel base URL": "WS \u9694\u9053\u57fa\u7840 URL",
                "WS direct public URL": "WS \u76f4\u8fde\u516c\u5f00 URL",
                "Reverse proxy profile": "\u53cd\u5411\u4ee3\u7406\u914d\u7f6e",
                "Backend host": "\u540e\u7aef\u4e3b\u673a",
                "Backend port": "\u540e\u7aef\u7aef\u53e3",
                "Push Hub License": "Push Hub License",
                "Enter Push Hub License Key": "\u8f93\u5165 Push Hub License Key",
                "Purchase Push Hub License": "\u8d2d\u4e70 Push Hub License",
                "Confirm License": "\u786e\u8ba4\u8bb8\u53ef\u8bc1",
                "Open Migration Docs (8000)": "\u6253\u5f00 Migration Docs (8000)",
                "Open Legacy Docs (8001)": "\u6253\u5f00 Legacy Docs (8001)",
                "Set LEGACY_PUBLIC_URL in Advanced Mode to enable 8001 reachability checks.": "\u5728 Advanced Mode \u4e2d\u8bbe\u7f6e LEGACY_PUBLIC_URL \u4ee5\u542f\u7528 8001 \u53ef\u8fbe\u6027\u68c0\u67e5\u3002",
                "Legacy backend (8001) is not reachable.\nMake sure totp.py has started it and Cloudflare Tunnel is configured.": "\u65e0\u6cd5\u8bbf\u95ee Legacy backend (8001)\u3002\n\u8bf7\u786e\u8ba4 totp.py \u5df2\u542f\u52a8\u4e14 Cloudflare Tunnel \u5df2\u6b63\u786e\u914d\u7f6e\u3002",
                "Checking legacy backend reachability...": "\u6b63\u5728\u68c0\u67e5 legacy backend \u53ef\u8fbe\u6027...",
                "Enabled": "\u5df2\u542f\u7528",
            },
            "ZH-TW": {
                "Simple mode": "\u7c21\u55ae\u6a21\u5f0f",
                "HTTPS and WebSocket are routed through the same Cloudflare Tunnel domain.": "HTTPS \u8207 WebSocket \u6703\u900f\u904e\u540c\u4e00\u500b Cloudflare Tunnel \u7db2\u57df\u8f49\u9001\u3002",
                "Simple mode is recommended for deployments with fewer than 10 users.\nAt larger scale, tunneled WebSocket stability can decrease.": "\u7c21\u55ae\u6a21\u5f0f\u5efa\u8b70\u7528\u65bc\u5c11\u65bc 10 \u4eba\u7684\u90e8\u7f72\u3002\n\u5927\u898f\u6a21\u6642\uff0c\u96a7\u9053 WebSocket \u7a69\u5b9a\u6027\u53ef\u80fd\u4e0b\u964d\u3002",
                "Advanced mode": "\u9032\u968e\u6a21\u5f0f",
                "Expose HTTPS and WebSocket separately, and tune runtime and backend environment mappings in detail.": "\u5206\u958b\u516c\u958b HTTPS \u8207 WebSocket\uff0c\u4e26\u7d30\u90e8\u8abf\u6574\u57f7\u884c\u53c3\u6578\u8207\u5f8c\u7aef\u74b0\u5883\u8b8a\u6578\u6620\u5c04\u3002",
                "Public transport": "\u516c\u958b\u50b3\u8f38",
                "Runtime": "\u57f7\u884c\u671f",
                "Storage and quotas": "\u5132\u5b58\u8207\u914d\u984d",
                "Rate limiting and security": "\u9650\u6d41\u8207\u5b89\u5168",
                "Performance": "\u6548\u80fd",
                "Legacy service access": "\u820a\u7248\u670d\u52d9\u5b58\u53d6",
                "Backend architecture (ports 8000 / 8001)": "\u5f8c\u7aef\u67b6\u69cb (8000 / 8001)",
                "Cloudflare Tunnel domain": "Cloudflare Tunnel \u7db2\u57df",
                "Per-user storage size (MB)": "\u6bcf\u4f4d\u4f7f\u7528\u8005\u5132\u5b58\u5927\u5c0f (MB)",
                "Public hub URL": "\u516c\u958b hub URL",
                "API public base URL": "API \u516c\u958b\u57fa\u5e95 URL",
                "WebSocket connection mode": "WebSocket \u9023\u7dda\u6a21\u5f0f",
                "WS tunnel base URL": "WS \u96a7\u9053\u57fa\u5e95 URL",
                "WS direct public URL": "WS \u76f4\u9023\u516c\u958b URL",
                "Reverse proxy profile": "\u53cd\u5411\u4ee3\u7406\u8a2d\u5b9a",
                "Backend host": "\u5f8c\u7aef\u4e3b\u6a5f",
                "Backend port": "\u5f8c\u7aef\u9023\u63a5\u57e0",
                "Push Hub License": "Push Hub License",
                "Enter Push Hub License Key": "\u8f38\u5165 Push Hub License Key",
                "Purchase Push Hub License": "\u8cfc\u8cb7 Push Hub License",
                "Confirm License": "\u78ba\u8a8d\u6388\u6b0a",
                "Open Migration Docs (8000)": "\u958b\u555f Migration Docs (8000)",
                "Open Legacy Docs (8001)": "\u958b\u555f Legacy Docs (8001)",
                "Set LEGACY_PUBLIC_URL in Advanced Mode to enable 8001 reachability checks.": "\u5728 Advanced Mode \u8a2d\u5b9a LEGACY_PUBLIC_URL \u4ee5\u555f\u7528 8001 \u53ef\u9054\u6027\u6aa2\u67e5\u3002",
                "Legacy backend (8001) is not reachable.\nMake sure totp.py has started it and Cloudflare Tunnel is configured.": "\u7121\u6cd5\u9023\u7dda Legacy backend (8001)\u3002\n\u8acb\u78ba\u8a8d totp.py \u5df2\u555f\u52d5\u4e14 Cloudflare Tunnel \u5df2\u5b8c\u6210\u8a2d\u5b9a\u3002",
                "Checking legacy backend reachability...": "\u6b63\u5728\u6aa2\u67e5 legacy backend \u53ef\u9054\u6027...",
                "Enabled": "\u5df2\u555f\u7528",
            },
        }
        return translations.get(lang, {}).get(text, text)

    def _update_simple_scroll_state(self) -> None:
        if self.simple_panel is None or self.simple_scroll_panel is None:
            return

        if not self.simple_panel.winfo_ismapped():
            return

        if self.simple_content_host is not None:
            self.update_idletasks()
            if self.simple_content_host.winfo_height() <= 1:
                self.after_idle(self._update_simple_scroll_state)
                return

        if not self.simple_scroll_panel.winfo_ismapped():
            self.simple_scroll_panel.grid()

    def _crypto_asset_label(self, asset_id: str) -> str:
        return self._tr(f"crypto_asset_{asset_id}")

    def _resolve_donation_qr_path(self) -> Optional[Path]:
        candidates = [
            self.project_dir / "tools" / "assets" / "donation.png",
            self.project_dir / "donation.png",
            self.project_dir.parent / "donation.png",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _open_card_support_modal(self) -> None:
        owner: ctk.CTkBaseClass = self.about_support_modal if self.about_support_modal is not None and self.about_support_modal.winfo_exists() else self
        top = self._create_support_modal(
            self._tr("card_modal_title"),
            int(520 * self.scale),
            int(620 * self.scale),
            owner,
        )

        ctk.CTkLabel(top, text=self._tr("card_modal_title"), text_color=TEXT, font=("Segoe UI Semibold", 22)).pack(anchor="w", padx=20, pady=(18, 10))
        qr_path = self._resolve_donation_qr_path()
        if qr_path is None:
            ctk.CTkLabel(top, text=self._tr("donation_qr_missing"), text_color=ERROR, font=("Segoe UI", 13)).pack(anchor="w", padx=20, pady=(8, 0))
            return
        try:
            image = Image.open(qr_path).convert("RGB")
            qr_img = ctk.CTkImage(light_image=image, dark_image=image, size=(420, 420))
            image_label = ctk.CTkLabel(top, text="", image=qr_img)
            image_label.image = qr_img
            image_label.pack(padx=20, pady=(6, 20), fill="both", expand=True)
        except Exception as exc:
            ctk.CTkLabel(top, text=str(exc), text_color=ERROR, font=("Segoe UI", 13), justify="left", wraplength=460).pack(anchor="w", padx=20, pady=(8, 0))

    def _open_crypto_support_modal(self) -> None:
        owner: ctk.CTkBaseClass = self.about_support_modal if self.about_support_modal is not None and self.about_support_modal.winfo_exists() else self
        top = self._create_support_modal(
            self._tr("crypto_modal_title"),
            int(620 * self.scale),
            int(700 * self.scale),
            owner,
        )
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(4, weight=1)

        selected_asset = tk.StringVar(value=CRYPTO_ASSETS[0][0])
        address_var = tk.StringVar(value=CRYPTO_ASSETS[0][1])

        ctk.CTkLabel(top, text=self._tr("crypto_modal_title"), text_color=TEXT, font=("Segoe UI Semibold", 22)).grid(row=0, column=0, padx=20, pady=(16, 8), sticky="w")
        ctk.CTkLabel(top, text=self._tr("crypto_select_asset"), text_color=MUTED, font=("Segoe UI Semibold", 12)).grid(row=1, column=0, padx=20, sticky="w")

        options = [asset_id for asset_id, _ in CRYPTO_ASSETS]
        labels = {asset_id: self._crypto_asset_label(asset_id) for asset_id in options}
        option_menu = ctk.CTkOptionMenu(top, values=[labels[asset_id] for asset_id in options])
        option_menu.grid(row=2, column=0, padx=20, pady=(6, 10), sticky="w")

        qr_label = ctk.CTkLabel(top, text="")
        qr_label.grid(row=4, column=0, padx=20, pady=(8, 10), sticky="n")

        ctk.CTkLabel(top, text=self._tr("crypto_wallet_address"), text_color=MUTED, font=("Segoe UI Semibold", 12)).grid(row=5, column=0, padx=20, sticky="w")
        address_entry = ctk.CTkEntry(top, textvariable=address_var, height=40, corner_radius=12)
        address_entry.grid(row=6, column=0, padx=20, pady=(6, 10), sticky="ew")

        def _copy_address() -> None:
            self.clipboard_clear()
            self.clipboard_append(address_var.get())
            messagebox.showinfo(self._tr("crypto_modal_title"), self._tr("address_copied"))

        ctk.CTkButton(top, text=self._tr("copy_address"), command=_copy_address, height=36, corner_radius=12, fg_color=ACCENT_SOFT, hover_color="#c5d8ff", text_color=ACCENT, font=("Segoe UI Semibold", 12)).grid(row=7, column=0, padx=20, pady=(0, 18), sticky="w")

        def _render_qr(asset_id: str) -> None:
            address = dict(CRYPTO_ASSETS).get(asset_id, "")
            address_var.set(address)
            qr = qrcode.QRCode(border=2, box_size=8)
            qr.add_data(address)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
            qr_img = ctk.CTkImage(light_image=image, dark_image=image, size=(280, 280))
            qr_label.configure(image=qr_img)
            qr_label.image = qr_img

        def _on_asset_changed(selected_label: str) -> None:
            for asset_id, label in labels.items():
                if label == selected_label:
                    selected_asset.set(asset_id)
                    _render_qr(asset_id)
                    break

        option_menu.configure(command=_on_asset_changed)
        option_menu.set(labels[CRYPTO_ASSETS[0][0]])
        _render_qr(CRYPTO_ASSETS[0][0])

    def _open_about_support_modal(self) -> None:
        if self.about_support_modal is not None and self.about_support_modal.winfo_exists():
            self.about_support_modal.lift()
            self.about_support_modal.focus_force()
            return

        top = self._create_support_modal(
            self._tr("about_support_title"),
            int(900 * self.scale),
            int(700 * self.scale),
            self,
        )
        self.about_support_modal = top
        top.protocol("WM_DELETE_WINDOW", self._close_about_support_modal)
        top.grid_columnconfigure(0, weight=1)
        top.grid_rowconfigure(0, weight=8)
        top.grid_rowconfigure(1, weight=2)

        upper = ctk.CTkFrame(top, fg_color=CARD, corner_radius=18, border_width=1, border_color=BORDER)
        upper.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="nsew")
        upper.grid_columnconfigure(0, weight=1)
        upper.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(upper, text=self._tr("about_support_title"), text_color=TEXT, font=("Segoe UI Semibold", 22)).grid(row=0, column=0, padx=18, pady=(14, 8), sticky="w")
        about_text = ctk.CTkTextbox(upper, corner_radius=12, border_width=1, border_color=BORDER, fg_color=CARD_ALT, text_color=TEXT, font=("Segoe UI", 13), wrap="word")
        about_text.grid(row=1, column=0, padx=18, pady=(0, 14), sticky="nsew")
        about_text.insert("1.0", self._tr("about_support_intro") + "\n\n" + self._tr("about_support_contact"))
        about_text.configure(state="disabled")

        lower = ctk.CTkFrame(top, fg_color=CARD, corner_radius=18, border_width=1, border_color=BORDER)
        lower.grid(row=1, column=0, padx=16, pady=(8, 16), sticky="nsew")
        lower.grid_columnconfigure(0, weight=1)
        lower.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(lower, text=self._tr("support_card_button"), command=self._open_card_support_modal, height=42, corner_radius=12, fg_color=ACCENT_SOFT, hover_color="#c5d8ff", text_color=ACCENT, font=("Segoe UI Semibold", 13)).grid(row=0, column=0, padx=(18, 10), pady=18, sticky="ew")
        ctk.CTkButton(lower, text=self._tr("support_crypto_button"), command=self._open_crypto_support_modal, height=42, corner_radius=12, fg_color=ACCENT, hover_color="#215bbd", font=("Segoe UI Semibold", 13)).grid(row=0, column=1, padx=(10, 18), pady=18, sticky="ew")

    def _close_about_support_modal(self) -> None:
        if self.about_support_modal is None:
            return
        try:
            self.about_support_modal.destroy()
        except Exception:
            pass
        self.about_support_modal = None

    def _create_support_modal(
        self,
        title: str,
        width: int,
        height: int,
        owner: ctk.CTkBaseClass,
    ) -> ctk.CTkToplevel:
        top = ctk.CTkToplevel(owner)
        top.title(title)
        top.geometry(f"{width}x{height}")
        top.configure(fg_color=SURFACE)
        top.transient(self)

        current_grab = self.grab_current()
        if current_grab is not None:
            try:
                current_grab.grab_release()
            except Exception:
                pass
        try:
            top.grab_set()
        except Exception:
            pass

        top.attributes("-topmost", True)
        top.lift()
        top.focus_force()
        return top

    def _fit_initial_simple_layout(self) -> None:
        if self.simple_layout_fitted:
            return
        if self.simple_panel is None:
            return

        self._apply_dashboard_layout()
        self._update_simple_scroll_state()
        self.simple_layout_fitted = True

    def _sync_mode_panels(self) -> None:
        mode = self._current_mode()
        if self.simple_panel is None or self.advanced_panel is None:
            return
        if mode == "advanced":
            self.simple_panel.grid_remove()
            self.advanced_panel.grid()
        else:
            self.advanced_panel.grid_remove()
            self.simple_panel.grid()
        self.after_idle(self._update_simple_scroll_state)
        self._update_architecture_warning()

    def _build_docs_url(self, host_source: str, port: int) -> str:
        host = _extract_host(host_source)
        if not host:
            return ""
        return f"http://{host}:{port}/docs"

    def _open_migration_docs_8000(self) -> None:
        public_source = str(self.vars["api_public_base_url"].get()).strip() or str(self.vars["public_hub_url"].get()).strip()
        docs_url = self._build_docs_url(public_source, 8000)
        if not docs_url:
            messagebox.showinfo("Migration docs", "Set API public base URL or Public hub URL in Advanced Mode first.")
            return
        webbrowser.open(docs_url)
        self._enqueue_log(f"[docs] Opening migration docs: {docs_url}")

    def _open_legacy_docs_8001(self) -> None:
        docs_url = self._build_docs_url(str(self.vars["legacy_public_url"].get()).strip(), 8001)
        if not docs_url:
            messagebox.showinfo("Legacy docs", "Set LEGACY_PUBLIC_URL in Advanced Mode first.")
            return
        webbrowser.open(docs_url)
        self._enqueue_log(f"[docs] Opening legacy docs: {docs_url}")

    def _resolve_legacy_api_base_url(self) -> str:
        mode = str(self.vars["mode"].get()).strip().lower() or "simple"
        if mode == "simple":
            host = str(self.vars["backend_host"].get()).strip() or "127.0.0.1"
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            return f"http://{host}:8000"

        legacy_public = _normalize_https_url(str(self.vars["legacy_public_url"].get()).strip())
        if legacy_public:
            return legacy_public
        host = str(self.vars["backend_host"].get()).strip() or "127.0.0.1"
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        return f"http://{host}:8001"

    def _prompt_admin_password(self) -> Optional[str]:
        password_var = tk.StringVar(value="")
        result: dict[str, Optional[str]] = {"password": None}

        top = ctk.CTkToplevel(self)
        top.title("Admin password")
        top.geometry("420x230")
        top.configure(fg_color=SURFACE)
        top.transient(self)
        top.grab_set()
        top.attributes("-topmost", True)
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top,
            text="Confirm admin password",
            text_color=TEXT,
            font=("Segoe UI Semibold", 20),
        ).grid(row=0, column=0, padx=22, pady=(22, 6), sticky="w")
        ctk.CTkLabel(
            top,
            text="The password is sent once through HTTP Basic auth and is not saved by the GUI.",
            text_color=MUTED,
            justify="left",
            wraplength=360,
            font=("Segoe UI", 13),
        ).grid(row=1, column=0, padx=22, pady=(0, 14), sticky="w")
        entry = ctk.CTkEntry(top, textvariable=password_var, height=42, corner_radius=12, show="*")
        entry.grid(row=2, column=0, padx=22, pady=(0, 14), sticky="ew")
        entry.focus_set()

        def submit() -> None:
            value = password_var.get()
            if not value:
                messagebox.showerror("Admin password", "Admin password is required.", parent=top)
                return
            result["password"] = value
            password_var.set("")
            top.destroy()

        entry.bind("<Return>", lambda _event: submit())
        ctk.CTkButton(
            top,
            text="Continue",
            command=submit,
            height=40,
            corner_radius=12,
            fg_color=ACCENT,
            hover_color="#215bbd",
            font=("Segoe UI Semibold", 13),
        ).grid(row=3, column=0, padx=22, pady=(0, 18), sticky="ew")

        top.wait_window()
        return result["password"]

    def _license_expiry_text(self) -> str:
        expires_on = str(self.vars["push_hub_license_expires_on"].get()).strip()
        if not expires_on:
            expires_on = "unknown"
        return f"License expires on: {expires_on}"

    def _set_license_expiry(self, raw_value: str) -> None:
        expires_on = self._extract_date(raw_value)
        if expires_on:
            self.vars["push_hub_license_expires_on"].set(expires_on)
        for label in self.license_expiry_labels:
            label.configure(text=self._ui_text(self._license_expiry_text()))

    def _extract_date(self, raw_value: str) -> str:
        value = str(raw_value or "").strip()
        if not value:
            return ""
        if "T" in value:
            value = value.split("T", 1)[0]
        if " " in value:
            value = value.split(" ", 1)[0]
        return value[:10] if len(value) >= 10 else value

    def _replace_textbox_value(self, textbox: ctk.CTkTextbox, value: str) -> None:
        textbox.delete("1.0", "end")
        textbox.insert("1.0", value)

    def _sync_notification_textboxes_from_vars(self) -> None:
        body = str(self.vars["server_notification_body"].get())
        extra_data = str(self.vars["server_notification_extra_data"].get())
        for textbox in self.notification_body_textboxes:
            self._replace_textbox_value(textbox, body)
        for textbox in self.notification_extra_textboxes:
            self._replace_textbox_value(textbox, extra_data)

    def _sync_notification_vars_from_textboxes(self) -> None:
        body_widgets = [widget for widget in self.notification_body_textboxes if widget.winfo_ismapped()]
        extra_widgets = [widget for widget in self.notification_extra_textboxes if widget.winfo_ismapped()]
        if body_widgets:
            self.vars["server_notification_body"].set(body_widgets[-1].get("1.0", "end-1c"))
        if extra_widgets:
            self.vars["server_notification_extra_data"].set(extra_widgets[-1].get("1.0", "end-1c"))

    def _load_notification_template(self) -> None:
        selected = str(self.vars["server_notification_type"].get()).strip()
        if selected == "Single Notification":
            payload_raw = str(self.vars["server_notification_last_single_payload"].get()).strip()
            if payload_raw:
                try:
                    payload = json.loads(payload_raw)
                except ValueError:
                    payload = {}
                if isinstance(payload, dict):
                    self.vars["server_notification_title"].set(str(payload.get("title") or "MiraiChat"))
                    self.vars["server_notification_body"].set(str(payload.get("body") or ""))
                    self.vars["server_notification_image_url"].set(str(payload.get("image_url") or ""))
                    extra_data = payload.get("extra_data", "")
                    self.vars["server_notification_extra_data"].set(
                        json.dumps(extra_data, ensure_ascii=False, indent=2) if isinstance(extra_data, (dict, list)) else str(extra_data or "")
                    )
        else:
            self.vars["server_notification_title"].set("MiraiChat")
            self.vars["server_notification_body"].set(WELCOME_NOTIFICATION_BODY)
            self.vars["server_notification_image_url"].set("")
            self.vars["server_notification_extra_data"].set("")
        self._sync_notification_textboxes_from_vars()

    def _save_welcome_notification_template(self, body: str) -> None:
        welcome_path = self.tools_dir / ".welcome_messages.json"
        existing: dict[str, Any] = {}
        if welcome_path.exists():
            try:
                parsed = json.loads(welcome_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    existing = parsed
            except Exception:
                existing = {}
        existing["en"] = body
        welcome_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    def _send_server_notification(self) -> None:
        self._sync_notification_vars_from_textboxes()
        title = str(self.vars["server_notification_title"].get()).strip()
        body = str(self.vars["server_notification_body"].get()).strip()
        image_url = str(self.vars["server_notification_image_url"].get()).strip()
        extra_data_raw = str(self.vars["server_notification_extra_data"].get()).strip()
        if not title or not body:
            messagebox.showerror("Server Notification", "title and body are required.")
            return
        if not self._has_admin_password():
            messagebox.showerror(
                "Server Notification",
                "Set the legacy admin password from the TOTP-protected admin setup before sending notifications.",
            )
            return

        extra_data: Any = {}
        if extra_data_raw:
            try:
                extra_data = json.loads(extra_data_raw)
            except ValueError as exc:
                messagebox.showerror("Server Notification", f"extra_data must be valid JSON:\n{exc}")
                return

        payload = {
            "title": title,
            "body": body,
            "image_url": image_url,
            "extra_data": extra_data,
        }
        if str(self.vars["server_notification_type"].get()).strip() == "Single Notification":
            self.vars["server_notification_last_single_payload"].set(json.dumps(payload, ensure_ascii=False))
        else:
            try:
                self._save_welcome_notification_template(body)
            except Exception as exc:
                messagebox.showerror("Server Notification", f"Unable to save welcome notification template:\n{exc}")
                return

        admin_password = self._prompt_admin_password()
        if not admin_password:
            return

        headers = {"Content-Type": "application/json"}
        try:
            totp_data = self.totp_store.load() or {}
            totp_secret = str(totp_data.get("secret", "")).strip()
        except Exception:
            totp_secret = ""
        if totp_secret:
            headers["x-admin-totp"] = pyotp.TOTP(totp_secret).now()

        api_base = self._resolve_legacy_api_base_url()
        try:
            response = requests.post(
                f"{api_base}/chat/admin/broadcast",
                json={"title": title, "body": body},
                auth=(str(self.vars["admin_doc_user"].get()).strip() or "admin", admin_password),
                headers=headers,
                timeout=20,
            )
            admin_password = ""
            response_payload: dict[str, Any] = {}
            if response.content:
                try:
                    parsed = response.json()
                    if isinstance(parsed, dict):
                        response_payload = parsed
                except ValueError:
                    response_payload = {}
        except Exception as exc:
            admin_password = ""
            messagebox.showerror("Server Notification", f"Notification request failed:\n{exc}")
            return

        self.config_store.save(self._snapshot_config())
        self._enqueue_log(f"[notification] Broadcast request -> {api_base}/chat/admin/broadcast ({response.status_code})")
        if response.status_code >= 400:
            detail = response_payload.get("detail") or response_payload.get("message") or response.text
            messagebox.showerror("Server Notification", str(detail or "Notification request failed."))
            return

        messagebox.showinfo("Server Notification", "Notification sent.")

    def _resolve_pushhub_api_base_url(self) -> str:
        mode = str(self.vars["mode"].get()).strip().lower() or "simple"
        if mode == "simple":
            return _normalize_https_url(str(self.vars["simple_tunnel_domain"].get()))

        preferred = _normalize_https_url(str(self.vars["api_public_base_url"].get()))
        return preferred

    def _on_pushhub_checkout_token(self, checkout_token: str) -> None:
        token = (checkout_token or "").strip()
        if not token:
            return
        worker = threading.Thread(
            target=self._redeem_pushhub_checkout_token_worker,
            args=(token,),
            daemon=True,
        )
        worker.start()

    def _redeem_pushhub_checkout_token_worker(self, checkout_token: str) -> None:
        api_base = self._resolve_pushhub_api_base_url()
        if not api_base:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Push Hub License",
                    "Set a public backend URL first (Simple tunnel domain or Advanced API public base URL).",
                ),
            )
            return

        try:
            response = requests.post(
                f"{api_base}/api/pushhub/redeem",
                json={"checkout_session_id": checkout_token},
                timeout=20,
            )
            payload: dict[str, Any] = {}
            if response.content:
                try:
                    parsed_payload = response.json()
                    if isinstance(parsed_payload, dict):
                        payload = parsed_payload
                except ValueError:
                    payload = {}
        except Exception as exc:
            self.after(
                0,
                lambda: messagebox.showerror("Push Hub License", f"Failed to redeem checkout token:\n{exc}"),
            )
            return

        self._enqueue_log(f"[pushhub] Redeem request -> {api_base}/api/pushhub/redeem ({response.status_code})")

        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Push Hub License",
                    str(detail or "Checkout token redemption failed."),
                ),
            )
            return

        license_value = ""
        license_secret = ""
        session_token = ""
        if isinstance(payload, dict):
            license_value = str(payload.get("license", "")).strip()
            license_secret = str(payload.get("license_secret") or payload.get("secret") or "").strip()
            session_token = str(payload.get("session_token") or "").strip()
        if not license_value:
            self.after(
                0,
                lambda: messagebox.showerror("Push Hub License", "Central backend did not return a license."),
            )
            return

        def _apply_redeemed_license() -> None:
            self.vars["push_hub_license"].set(license_value)
            self.vars["push_hub_license_secret"].set(license_secret)
            if session_token:
                self.vars["central_server_token"].set(session_token)
            self._enqueue_log("[pushhub] License redeemed and autofilled; starting confirm flow")
            self.after(120, self._confirm_push_hub_license)

        self.after(0, _apply_redeemed_license)

    def _purchase_push_hub_license(self) -> None:
        if not self.pushhub_callback_server.start():
            messagebox.showerror("Push Hub License", "Unable to start local callback server on 127.0.0.1.")
            return

        callback_port = self.pushhub_callback_server.port
        if callback_port is None or int(callback_port) <= 0:
            messagebox.showerror("Push Hub License", "Local callback port is unavailable.")
            return

        redirect_url = f"http://127.0.0.1:{int(callback_port)}/pushhub"
        encoded_callback = quote(redirect_url, safe="")
        donate_url = f"https://donate.miraichat.net/donate?mode=pushhub&redirect={encoded_callback}"
        webbrowser.open(donate_url)
        self._enqueue_log(f"[pushhub] Opened purchase page: {donate_url}")
        messagebox.showinfo(
            "Push Hub License",
            "Complete payment in your browser. The launcher will automatically receive and redeem the callback token.",
        )

    def _confirm_push_hub_license(self) -> None:
        license_value = str(self.vars["push_hub_license"].get()).strip()
        license_secret = str(self.vars["push_hub_license_secret"].get()).strip()
        if not license_value:
            messagebox.showerror("Push Hub License", "Enter Push Hub License before confirmation.")
            return
        if not license_secret:
            messagebox.showerror("Push Hub License", "Enter Push Hub License Secret before confirmation.")
            return

        api_base = self._resolve_pushhub_api_base_url()
        if not api_base:
            messagebox.showerror(
                "Push Hub License",
                "Set a public backend URL first (Simple tunnel domain or Advanced API public base URL).",
            )
            return

        try:
            response = requests.post(
                f"{api_base}/api/pushhub/authorize",
                json={
                    "license_key": license_value,
                    "license_secret": license_secret,
                    "server_public_url": api_base,
                },
                timeout=15,
            )
            payload: dict[str, Any] = {}
            raw_text = response.text or ""
            if response.content:
                try:
                    parsed_payload = response.json()
                    if isinstance(parsed_payload, dict):
                        payload = parsed_payload
                except ValueError:
                    preview = raw_text.strip().replace("\n", " ")[:220]
                    self._enqueue_log(
                        f"[pushhub] Authorize returned non-JSON response ({response.status_code}) from {api_base}/api/pushhub/authorize"
                    )
                    message = (
                        "License confirmation failed: backend returned a non-JSON response.\n\n"
                        f"URL: {api_base}/api/pushhub/authorize\n"
                        f"HTTP: {response.status_code}\n"
                    )
                    if preview:
                        message += f"Body preview: {preview}"
                    messagebox.showerror("Push Hub License", message)
                    return
        except Exception as exc:
            messagebox.showerror("Push Hub License", f"License confirmation failed:\n{exc}")
            return

        self._enqueue_log(f"[pushhub] Authorize request -> {api_base}/api/pushhub/authorize ({response.status_code})")

        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            message = str(detail or payload.get("message") or "License confirmation failed.") if isinstance(payload, dict) else "License confirmation failed."
            messagebox.showerror("Push Hub License", message)
            return

        is_valid = bool(payload.get("ok")) if isinstance(payload, dict) else False
        if not is_valid:
            central_msg = "Invalid license. Please verify your key and retry."
            if isinstance(payload, dict):
                central_msg = str(payload.get("message") or payload.get("detail") or payload.get("error") or central_msg)
            messagebox.showerror("Push Hub License", central_msg)
            return

        session_token = str(
            payload.get("central_server_token") or payload.get("session_token") or ""
        ).strip() if isinstance(payload, dict) else ""
        token_expires_at = str(payload.get("token_expires_at") or "").strip() if isinstance(payload, dict) else ""
        returned_secret = str(payload.get("license_secret") or payload.get("secret") or "").strip() if isinstance(payload, dict) else ""
        license_expiry = str(
            payload.get("expiry") or payload.get("expires_at") or payload.get("license_expires_at") or ""
        ).strip() if isinstance(payload, dict) else ""
        server_id = str(payload.get("server_id") or "").strip() if isinstance(payload, dict) else ""
        pem_fingerprint = str(payload.get("pem_fingerprint") or "").strip() if isinstance(payload, dict) else ""
        pem_saved = bool(payload.get("pem_saved")) if isinstance(payload, dict) else False
        bootstrap_ok = bool(payload.get("bootstrap_ok")) if isinstance(payload, dict) else False
        if returned_secret:
            self.vars["push_hub_license_secret"].set(returned_secret)
        self._set_license_expiry(license_expiry)
        if session_token:
            self.vars["central_server_token"].set(session_token)
        if server_id:
            self.vars["push_hub_server_id"].set(server_id)
        if pem_fingerprint:
            self.vars["push_hub_pem_fingerprint"].set(pem_fingerprint)

        self.config_store.save(self._snapshot_config())
        self._enqueue_log(
            "[pushhub] License validated via central "
            f"server_id={server_id or '<missing>'} "
            f"pem_fingerprint={pem_fingerprint or '<missing>'} "
            f"pem_saved={pem_saved} "
            f"bootstrap_ok={bootstrap_ok}"
        )
        message_lines = [
            "License validated.",
            self._license_expiry_text(),
            f"server_id: {server_id or 'not returned'}",
            f"pem_fingerprint: {pem_fingerprint or 'not returned'}",
            f"PEM saved: {'yes' if pem_saved else 'already issued or not returned'}",
            f"Bootstrap: {'ok' if bootstrap_ok else 'not completed'}",
        ]
        if token_expires_at:
            message_lines.append(f"central_server_token expires_at: {token_expires_at}")
        messagebox.showinfo("Push Hub License", "\n".join(message_lines))

    def _is_legacy_backend_reachable(self, host: str) -> bool:
        if not host:
            return False
        try:
            with socket.create_connection((host, 8001), timeout=0.8):
                pass
            return True
        except Exception:
            return False

    def _update_architecture_warning(self) -> None:
        if self.architecture_warning_label is None:
            return
        mode = self.vars["mode"].get().strip().lower()
        if mode != "advanced":
            self.architecture_warning_label.configure(text="")
            return

        host = _extract_host(str(self.vars["legacy_public_url"].get()).strip())
        if not host:
            self.architecture_warning_label.configure(
                text=self._ui_text(
                    "Set LEGACY_PUBLIC_URL in Advanced Mode to enable 8001 reachability checks."
                )
            )
            return

        now = time.time()
        cached = self.legacy_status_cache.get(host)
        if cached and (now - cached[0]) <= 10.0:
            reachable = cached[1]
            if not reachable:
                self.architecture_warning_label.configure(
                    text=self._ui_text(
                        "Legacy backend (8001) is not reachable.\n"
                        "Make sure totp.py has started it and Cloudflare Tunnel is configured."
                    )
                )
            else:
                self.architecture_warning_label.configure(text="")
            return

        self.architecture_warning_label.configure(text=self._ui_text("Checking legacy backend reachability..."))
        if self.legacy_check_thread is None or not self.legacy_check_thread.is_alive():
            self.legacy_check_thread = threading.Thread(
                target=self._probe_legacy_backend_reachability,
                args=(host,),
                daemon=True,
            )
            self.legacy_check_thread.start()

    def _probe_legacy_backend_reachability(self, host: str) -> None:
        if not host:
            return
        reachable = self._is_legacy_backend_reachable(host)
        self.legacy_status_cache[host] = (time.time(), reachable)
        self.after(0, self._update_architecture_warning)

    def _snapshot_config(self) -> dict[str, Any]:
        self._sync_notification_vars_from_textboxes()
        snapshot: dict[str, Any] = {}
        for key, variable in self.vars.items():
            snapshot[key] = variable.get()
        return snapshot

    def _build_launch_plan(self, *, require_complete: bool) -> LaunchPlan:
        config = self._snapshot_config()
        mode = str(config["mode"]).strip().lower() or "simple"
        host = str(config["backend_host"]).strip() or DEFAULT_HOST
        port = _safe_int(str(config["backend_port"]), default=DEFAULT_PORT, minimum=1)
        reload_enabled = _as_bool(config["reload"])
        log_level = str(config["log_level"]).strip().lower() or DEFAULT_LOG_LEVEL
        access_log = _as_bool(config["access_log"])

        env: dict[str, str] = {}
        public_api_url = ""
        public_ws_url = ""
        ws_mode = "tunnel"

        if mode == "simple":
            domain = _normalize_https_url(str(config["simple_tunnel_domain"]))
            storage_mb = _safe_int(str(config["simple_storage_mb"]), default=DEFAULT_STORAGE_MB, minimum=1)
            if require_complete and not domain:
                raise ValueError("Simple Mode requires a Cloudflare Tunnel domain.")
            public_api_url = domain
            public_ws_url = _to_ws_url(domain) if domain else ""
            env.update(
                {
                    "WS_CONNECTION_MODE": "tunnel",
                    "DEFAULT_STORAGE_LIMIT": str(storage_mb * 1024 * 1024),
                    "PRIVATE_SERVER_STORAGE_LIMIT_MB": str(storage_mb),
                }
            )
            if domain:
                env["API_PUBLIC_BASE_URL"] = domain
                env["WS_TUNNEL_BASE_URL"] = public_ws_url
                env["CLOUDFLARE_DOMAIN"] = domain
            hub_public_base = _normalize_https_url(str(config.get("public_hub_url", ""))) or "https://public.miraichat.net"
            env["PUBLIC_HUB_URL"] = hub_public_base
            ws_mode = "tunnel"
        else:
            public_hub_url = _normalize_https_url(str(config["public_hub_url"]))
            api_public_base_url = _normalize_https_url(str(config["api_public_base_url"]))
            ws_mode = str(config["ws_connection_mode"]).strip().lower() or "tunnel"
            ws_tunnel_base_url = str(config["ws_tunnel_base_url"]).strip()
            ws_direct_public_url = str(config["ws_direct_public_url"]).strip()
            if ws_mode == "direct" and require_complete and not ws_direct_public_url:
                raise ValueError("Advanced Mode in direct WS mode requires a direct WebSocket URL.")

            public_api_url = api_public_base_url or public_hub_url
            if ws_mode == "direct":
                public_ws_url = ws_direct_public_url
            else:
                public_ws_url = ws_tunnel_base_url or _to_ws_url(public_api_url)

            env.update(
                {
                    "IS_PUBLIC_HUB": "true" if _as_bool(config["is_public_hub"]) else "false",
                    "WS_CONNECTION_MODE": ws_mode,
                    "DEFAULT_STORAGE_LIMIT": str(
                        _safe_int(str(config["default_storage_limit_mb"]), default=DEFAULT_STORAGE_MB, minimum=1) * 1024 * 1024
                    ),
                    "PRIVATE_SERVER_STORAGE_LIMIT_MB": str(
                        _safe_int(str(config["private_server_storage_limit_mb"]), default=DEFAULT_STORAGE_MB, minimum=1)
                    ),
                    "DONATION_MONTHLY_STORAGE_LIMIT_MB": str(
                        _safe_int(str(config["donation_monthly_storage_limit_mb"]), default=200, minimum=1)
                    ),
                    "UPLOAD_DIR": str(config["upload_dir"]).strip() or "stored_files",
                    "FILE_RETENTION_DAYS": str(
                        _safe_int(str(config["file_retention_days"]), default=3, minimum=1)
                    ),
                    "OFFLINE_MSG_RETENTION_DAYS": str(
                        _safe_int(str(config["offline_msg_retention_days"]), default=7, minimum=1)
                    ),
                    "REGISTER_RATE_LIMIT_MAX": str(
                        _safe_int(str(config["register_rate_limit_max"]), default=10, minimum=1)
                    ),
                    "REGISTER_RATE_LIMIT_WINDOW": str(
                        _safe_int(str(config["register_rate_limit_window"]), default=3600, minimum=1)
                    ),
                    "PIN_SYNC_AUTO_DEFAULT": "true" if _as_bool(config["pin_sync_auto_default"]) else "false",
                    "PIN_SYNC_FORCE_MANUAL": "true" if _as_bool(config["pin_sync_force_manual"]) else "false",
                    "PRESENCE_BACKEND": str(config["presence_backend"]).strip() or "memory",
                    "FANOUT_BACKEND": str(config["fanout_backend"]).strip() or "memory",
                    "REGION_POLICY_MODE": str(config["region_policy_mode"]).strip() or "auto",
                    "DEFAULT_REGION": str(config["default_region"]).strip() or "global",
                    "CHINA_FCM_POLICY": str(config["china_fcm_policy"]).strip() or "disable",
                }
            )

            if public_hub_url:
                env["PUBLIC_HUB_URL"] = public_hub_url
            if api_public_base_url:
                env["API_PUBLIC_BASE_URL"] = api_public_base_url
            if ws_tunnel_base_url:
                env["WS_TUNNEL_BASE_URL"] = ws_tunnel_base_url
            elif ws_mode == "tunnel" and public_ws_url:
                env["WS_TUNNEL_BASE_URL"] = public_ws_url
            if ws_direct_public_url:
                env["WS_DIRECT_PUBLIC_URL"] = ws_direct_public_url
            if str(config["presence_redis_url"]).strip():
                env["PRESENCE_REDIS_URL"] = str(config["presence_redis_url"]).strip()
            if str(config["fanout_redis_url"]).strip():
                env["FANOUT_REDIS_URL"] = str(config["fanout_redis_url"]).strip()
            if str(config["registration_key"]).strip():
                env["REGISTRATION_KEY"] = str(config["registration_key"]).strip()
            if str(config["server_identity_salt"]).strip():
                env["SERVER_IDENTITY_SALT"] = str(config["server_identity_salt"]).strip()
            if str(config["admin_ip_allowlist"]).strip():
                env["ADMIN_IP_ALLOWLIST"] = str(config["admin_ip_allowlist"]).strip()
            if str(config["cloudflare_domain"]).strip():
                env["CLOUDFLARE_DOMAIN"] = _normalize_https_url(str(config["cloudflare_domain"]))
            if str(config["legacy_public_url"]).strip():
                env["LEGACY_PUBLIC_URL"] = _normalize_https_url(str(config["legacy_public_url"]))

        push_hub_license = str(config.get("push_hub_license", "")).strip()
        if push_hub_license:
            env["HUB_LICENSE_KEY"] = push_hub_license
            env["PUSH_HUB_LICENSE"] = push_hub_license
        central_server_token = str(config.get("central_server_token", "")).strip()
        if central_server_token:
            env["CENTRAL_SERVER_TOKEN"] = central_server_token
        push_hub_server_id = str(config.get("push_hub_server_id", "")).strip()
        if push_hub_server_id:
            env["PUSH_DELEGATED_SERVER_ID"] = push_hub_server_id
        push_hub_pem_fingerprint = str(config.get("push_hub_pem_fingerprint", "")).strip()
        if push_hub_pem_fingerprint:
            env["SERVER_SIGNING_KEY_FINGERPRINT"] = push_hub_pem_fingerprint

        admin_user = str(config.get("admin_doc_user", "")).strip() or "admin"
        admin_hash = str(config.get("admin_pass_hash", "")).strip()
        if admin_user:
            env["ADMIN_DOC_USER"] = admin_user
        if admin_hash:
            env["ADMIN_PASS_HASH"] = admin_hash
        try:
            totp_data = self.totp_store.load() or {}
            admin_totp_secret = str(totp_data.get("secret", "")).strip()
        except Exception:
            admin_totp_secret = ""
        if admin_totp_secret:
            env["ADMIN_TOTP_SECRET"] = admin_totp_secret

        return LaunchPlan(
            mode=mode,
            host=host,
            port=port,
            reload_enabled=reload_enabled,
            log_level=log_level,
            access_log=access_log,
            env=env,
            public_api_url=public_api_url,
            public_ws_url=public_ws_url,
            ws_mode=ws_mode,
        )

    def _save_config(self) -> None:
        try:
            self._build_launch_plan(require_complete=False)
        except Exception as exc:
            messagebox.showerror("Configuration", str(exc))
            return
        snapshot = self._snapshot_config()
        self.config_store.save(snapshot)
        self._enqueue_log("[config] Configuration saved to tools/.backend_gui_config.json")
        messagebox.showinfo("Configuration", "Configuration saved.")

    def _start_backend(self) -> None:
        try:
            plan = self._build_launch_plan(require_complete=True)
        except Exception as exc:
            messagebox.showerror("Configuration", str(exc))
            return

        try:
            self.config_store.save(self._snapshot_config())
            self.process_controller.start(plan)
        except Exception as exc:
            messagebox.showerror("Backend start", str(exc))
            return
        self._schedule_status_probe(force=True)

    def _stop_backend(self) -> None:
        if not self.process_controller.is_running():
            messagebox.showinfo("Backend", "The launcher is not currently managing a running backend process.")
            return
        self.process_controller.stop()
        self._schedule_status_probe(force=True)

    def _enqueue_log(self, message: str) -> None:
        if message:
            self.log_queue.put(message)

    def _drain_logs(self) -> None:
        if self.logs_textbox is not None:
            self.logs_textbox.configure(state="normal")
            drained = False
            while True:
                try:
                    line = self.log_queue.get_nowait()
                except queue.Empty:
                    break
                drained = True
                self.logs_textbox.insert("end", line + "\n")
            if drained:
                self.logs_textbox.see("end")
            self.logs_textbox.configure(state="disabled")
        self.after(150, self._drain_logs)

    def _schedule_status_probe(self, *, force: bool = False) -> None:
        if not self.authenticated:
            self.after(1200, self._schedule_status_probe)
            return
        if self.status_thread is not None and self.status_thread.is_alive() and not force:
            self.after(3000, self._schedule_status_probe)
            return
        self.status_thread = threading.Thread(target=self._run_status_probe, daemon=True)
        self.status_thread.start()
        self.after(5000, self._schedule_status_probe)

    def _run_status_probe(self) -> None:
        try:
            plan = self._build_launch_plan(require_complete=False)
            snapshot = self.status_probe.probe(plan, self.process_controller.is_running())
        except Exception as exc:
            snapshot = StatusSnapshot(
                backend_text="Status probe failed",
                backend_color=ERROR,
                tunnel_text="Unknown",
                tunnel_color=MUTED,
                ws_text="Unknown",
                ws_color=MUTED,
                detail_lines=[str(exc)],
            )
        self.after(0, lambda: self._apply_status_snapshot(snapshot))

    def _apply_status_snapshot(self, snapshot: StatusSnapshot) -> None:
        if self.backend_status_value is None or self.tunnel_status_value is None:
            return
        self.backend_status_value.configure(text=f"Backend: {snapshot.backend_text}", text_color=snapshot.backend_color)
        self.tunnel_status_value.configure(text=f"Tunnel: {snapshot.tunnel_text}", text_color=snapshot.tunnel_color)
        if self.ws_status_scroll is not None:
            self.ws_status_scroll.configure(state="normal", text_color=snapshot.ws_color)
            self.ws_status_scroll.delete("1.0", "end")
            self.ws_status_scroll.insert("1.0", f"WebSocket: {snapshot.ws_text}")
            self.ws_status_scroll.configure(state="disabled")
        if self.status_detail_value is not None:
            self.status_detail_value.configure(state="normal")
            self.status_detail_value.delete("1.0", "end")
            self.status_detail_value.insert("1.0", "\n".join(snapshot.detail_lines))
            self.status_detail_value.configure(state="disabled")
        self._update_architecture_warning()

    def _on_close(self) -> None:
        if self.process_controller.is_running():
            should_close = messagebox.askyesno(
                "Exit launcher",
                "The backend process started by this launcher is still running. Stop it and close the window?",
            )
            if not should_close:
                return
            self.process_controller.stop()
        self.pushhub_callback_server.stop()
        self.destroy()


def run() -> None:
    app = BackendControlApp()
    app.mainloop()


if __name__ == "__main__":
    run()
