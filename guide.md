# Backend GUI Field Guide

This guide explains all input fields used in Simple Mode and Advanced Mode across both GUI entry points:

- tools/backend_gui_app.py
- custom_backend/tools/backend_gui_app.py

## English

| Field | What it represents and how it is used |
| --- | --- |
| Cloudflare Tunnel domain (Simple) | The public HTTPS domain exposed through Cloudflare Tunnel. In Simple Mode it is used as the main external access address. |
| Per-user storage size (MB) (Simple) | Default storage allowance per user. Written into storage-related backend limits. |
| Push Hub License (Simple, custom backend GUI) | License string for Push Hub features. Used by the custom backend launcher to validate and persist commercial Push Hub access. |
| Public hub URL | Public HTTPS base URL announced to clients and federation logic. |
| API public base URL | Public API base URL clients should call for backend API access. |
| WebSocket connection mode | Selects whether WebSocket is served through tunnel mode or direct public WS endpoint mode. |
| WS tunnel base URL | WebSocket URL used when connection mode is tunnel. |
| WS direct public URL | WebSocket URL used when connection mode is direct. |
| Reverse proxy profile | Operator profile hint (cloudflare/nginx/other). Helps document deployment intent in config. |
| Cloudflare Tunnel domain (Advanced) | Optional reference domain for Cloudflare Tunnel deployments in Advanced Mode. |
| Backend host | Host value passed to uvicorn bind settings. |
| Backend port | Port value passed to uvicorn bind settings. |
| Reload code on change | Enables/disables uvicorn reload mode for development-style restarts. |
| Log level | Runtime log verbosity level for uvicorn. |
| Access log | Enables/disables uvicorn access logs. |
| Default storage limit (MB) | Default storage quota for new users. |
| Private server storage limit (MB) | Storage limit for private server policy paths. |
| Donation storage limit (MB) | Storage limit applied to donation-tier policy paths. |
| Upload directory | Filesystem path used for uploaded file storage. |
| File retention days | Number of days to keep uploaded files before cleanup rules apply. |
| Offline message retention days | Number of days to keep offline messages before retention cleanup. |
| Register rate limit max | Maximum allowed registration attempts within the configured window. |
| Register rate limit window (seconds) | Time window (seconds) used with registration rate-limit max. |
| Registration key | Optional shared key required for controlled registration onboarding. |
| Server identity salt | Optional server-side identity salt override for identity derivation logic. |
| Admin IP allowlist | Comma-separated admin source IP allowlist for admin endpoint protection. |
| Treat this server as a public hub | Marks server behavior as public hub mode in backend flags. |
| PIN sync auto default | Default policy for automatic PIN sync behavior. |
| Force manual PIN sync | Forces manual PIN sync instead of automatic sync. |
| Push queue workers (tools GUI) | Worker count for push queue processing concurrency. |
| Push queue max size (tools GUI) | Maximum buffered push queue size before backpressure/drop policy applies. |
| Presence backend | Backend type for presence state storage (memory/redis). |
| Presence Redis URL | Redis connection URL used when presence backend is redis. |
| Fanout backend | Backend type for fanout/distribution storage (memory/redis). |
| Fanout Redis URL | Redis connection URL used when fanout backend is redis. |
| Region policy mode | Region routing policy mode (auto/manual). |
| Default region | Fallback/default region identifier for region policy. |
| China FCM policy | Policy controlling FCM handling behavior for China-related routing cases. |
| Push Hub License (Advanced, custom backend GUI) | License string field in Advanced Mode for purchasing/confirming Push Hub functionality. |
| LEGACY_PUBLIC_URL | Public URL used by the legacy backend docs/open helpers. |

## Japanese

| Field | 意味と用途 |
| --- | --- |
| Cloudflare Tunnel domain (Simple) | Cloudflare Tunnel で公開する HTTPS ドメインです。Simple Mode では主な外部アクセス先として使われます。 |
| Per-user storage size (MB) (Simple) | ユーザーごとの既定ストレージ容量です。バックエンドの容量制限設定に反映されます。 |
| Push Hub License (Simple, custom backend GUI) | Push Hub 機能のライセンス文字列です。custom backend ランチャーで検証・保存に使われます。 |
| Public hub URL | クライアントと連携処理に公開する HTTPS ベース URL です。 |
| API public base URL | クライアントが API 呼び出しに使う公開 API ベース URL です。 |
| WebSocket connection mode | WebSocket をトンネル経由にするか、公開 WS エンドポイント直結にするかを選択します。 |
| WS tunnel base URL | 接続モードが tunnel のときに使う WebSocket URL です。 |
| WS direct public URL | 接続モードが direct のときに使う WebSocket URL です。 |
| Reverse proxy profile | 運用メモ用のプロファイル設定です（cloudflare/nginx/other）。 |
| Cloudflare Tunnel domain (Advanced) | Advanced Mode で Cloudflare Tunnel 運用時に参照する任意ドメインです。 |
| Backend host | uvicorn のバインド先ホスト設定です。 |
| Backend port | uvicorn のバインド先ポート設定です。 |
| Reload code on change | uvicorn のリロードモード（開発向け自動再起動）を有効/無効にします。 |
| Log level | uvicorn のログ出力レベルです。 |
| Access log | uvicorn のアクセスログ出力を有効/無効にします。 |
| Default storage limit (MB) | 新規ユーザーに適用する既定ストレージ上限です。 |
| Private server storage limit (MB) | プライベートサーバーポリシー系に適用される容量上限です。 |
| Donation storage limit (MB) | 寄付関連ポリシー系に適用される容量上限です。 |
| Upload directory | アップロードファイルを保存するディレクトリパスです。 |
| File retention days | アップロードファイルを保持する日数です。 |
| Offline message retention days | オフラインメッセージを保持する日数です。 |
| Register rate limit max | 登録レート制限ウィンドウ内で許可する最大試行回数です。 |
| Register rate limit window (seconds) | 登録レート制限で使う時間窓（秒）です。 |
| Registration key | 登録制御に使う任意の共有キーです。 |
| Server identity salt | サーバー識別子導出ロジックに使う任意のソルト上書き値です。 |
| Admin IP allowlist | 管理エンドポイント保護に使う IP 許可リスト（カンマ区切り）です。 |
| Treat this server as a public hub | サーバーを公開ハブとして動作させるフラグです。 |
| PIN sync auto default | PIN 自動同期の既定ポリシーを設定します。 |
| Force manual PIN sync | PIN 同期を自動ではなく手動に強制します。 |
| Push queue workers (tools GUI) | プッシュキュー処理の並列ワーカー数です。 |
| Push queue max size (tools GUI) | プッシュキューの最大バッファ件数です。 |
| Presence backend | Presence 状態保存のバックエンド種別（memory/redis）です。 |
| Presence Redis URL | Presence backend が redis のときに使う Redis 接続 URL です。 |
| Fanout backend | Fanout 配信保存のバックエンド種別（memory/redis）です。 |
| Fanout Redis URL | Fanout backend が redis のときに使う Redis 接続 URL です。 |
| Region policy mode | リージョン振り分けポリシーのモード（auto/manual）です。 |
| Default region | リージョンポリシーの既定リージョンです。 |
| China FCM policy | 中国向けルーティング時の FCM 処理ポリシーです。 |
| Push Hub License (Advanced, custom backend GUI) | Advanced Mode で Push Hub 機能の購入/確認に使うライセンス文字列です。 |
| LEGACY_PUBLIC_URL | レガシーバックエンドの docs オープン補助で使う公開 URL です。 |

## Traditional Chinese (ZH-TW)

| Field | 欄位說明與用途 |
| --- | --- |
| Cloudflare Tunnel domain (Simple) | 透過 Cloudflare Tunnel 對外公開的 HTTPS 網域。Simple Mode 會以它作為主要外部入口。 |
| Per-user storage size (MB) (Simple) | 每位使用者的預設儲存容量，會寫入後端容量限制設定。 |
| Push Hub License (Simple, custom backend GUI) | Push Hub 功能授權字串。custom backend 啟動器會用它做驗證與保存。 |
| Public hub URL | 對客戶端與聯邦流程公告的公開 HTTPS 基礎 URL。 |
| API public base URL | 客戶端呼叫後端 API 使用的公開基礎 URL。 |
| WebSocket connection mode | 選擇 WebSocket 走 tunnel 或 direct 公開 WS 端點。 |
| WS tunnel base URL | 當連線模式為 tunnel 時使用的 WebSocket URL。 |
| WS direct public URL | 當連線模式為 direct 時使用的 WebSocket URL。 |
| Reverse proxy profile | 運維用的反向代理檔案標記（cloudflare/nginx/other），主要用於配置說明。 |
| Cloudflare Tunnel domain (Advanced) | Advanced Mode 下 Cloudflare Tunnel 部署的可選參考網域。 |
| Backend host | 傳給 uvicorn 的綁定主機位址。 |
| Backend port | 傳給 uvicorn 的綁定連接埠。 |
| Reload code on change | 啟用/停用 uvicorn reload 模式（程式變更自動重啟）。 |
| Log level | uvicorn 執行時日誌等級。 |
| Access log | 啟用/停用 uvicorn access log。 |
| Default storage limit (MB) | 新使用者預設儲存配額。 |
| Private server storage limit (MB) | 私有伺服器策略路徑使用的儲存上限。 |
| Donation storage limit (MB) | 捐助等級策略路徑使用的儲存上限。 |
| Upload directory | 上傳檔案儲存目錄路徑。 |
| File retention days | 上傳檔案保留天數。 |
| Offline message retention days | 離線訊息保留天數。 |
| Register rate limit max | 註冊限流時間窗內允許的最大嘗試次數。 |
| Register rate limit window (seconds) | 註冊限流使用的時間窗（秒）。 |
| Registration key | 受控註冊流程使用的可選共用金鑰。 |
| Server identity salt | 伺服器身份推導邏輯可選的 salt 覆寫值。 |
| Admin IP allowlist | 管理端點保護使用的 IP 白名單（逗號分隔）。 |
| Treat this server as a public hub | 將伺服器標記為公開 hub 模式。 |
| PIN sync auto default | 設定 PIN 自動同步的預設策略。 |
| Force manual PIN sync | 強制 PIN 同步改為手動而非自動。 |
| Push queue workers (tools GUI) | 推送佇列處理並行工作執行緒數量。 |
| Push queue max size (tools GUI) | 推送佇列可緩衝的最大大小。 |
| Presence backend | Presence 狀態儲存後端類型（memory/redis）。 |
| Presence Redis URL | 當 Presence backend 為 redis 時使用的 Redis 連線 URL。 |
| Fanout backend | Fanout 分發儲存後端類型（memory/redis）。 |
| Fanout Redis URL | 當 Fanout backend 為 redis 時使用的 Redis 連線 URL。 |
| Region policy mode | 區域路由策略模式（auto/manual）。 |
| Default region | 區域策略的預設區域識別值。 |
| China FCM policy | 中國相關路由情境下的 FCM 處理策略。 |
| Push Hub License (Advanced, custom backend GUI) | Advanced Mode 中用於購買/確認 Push Hub 功能的授權字串。 |
| LEGACY_PUBLIC_URL | 舊版後端 docs 開啟輔助功能使用的公開 URL。 |

## Simplified Chinese (ZH-CN)

| Field | 字段说明与用途 |
| --- | --- |
| Cloudflare Tunnel domain (Simple) | 通过 Cloudflare Tunnel 对外公开的 HTTPS 域名。Simple Mode 中作为主要外部入口。 |
| Per-user storage size (MB) (Simple) | 每个用户的默认存储额度，会写入后端存储限制配置。 |
| Push Hub License (Simple, custom backend GUI) | Push Hub 功能授权字符串。custom backend 启动器使用它进行校验与持久化。 |
| Public hub URL | 向客户端和联邦流程公告的公开 HTTPS 基础 URL。 |
| API public base URL | 客户端访问后端 API 使用的公开基础 URL。 |
| WebSocket connection mode | 选择 WebSocket 使用 tunnel 模式或 direct 公网 WS 端点模式。 |
| WS tunnel base URL | 当连接模式为 tunnel 时使用的 WebSocket URL。 |
| WS direct public URL | 当连接模式为 direct 时使用的 WebSocket URL。 |
| Reverse proxy profile | 反向代理配置档位（cloudflare/nginx/other），主要用于运维配置说明。 |
| Cloudflare Tunnel domain (Advanced) | Advanced Mode 下 Cloudflare Tunnel 部署的可选参考域名。 |
| Backend host | 传给 uvicorn 的绑定主机地址。 |
| Backend port | 传给 uvicorn 的绑定端口。 |
| Reload code on change | 启用/关闭 uvicorn reload 模式（代码变更自动重启）。 |
| Log level | uvicorn 运行日志级别。 |
| Access log | 启用/关闭 uvicorn 访问日志。 |
| Default storage limit (MB) | 新用户默认存储配额。 |
| Private server storage limit (MB) | 私有服务器策略路径使用的存储上限。 |
| Donation storage limit (MB) | 捐赠档位策略路径使用的存储上限。 |
| Upload directory | 上传文件保存目录路径。 |
| File retention days | 上传文件保留天数。 |
| Offline message retention days | 离线消息保留天数。 |
| Register rate limit max | 注册限流窗口内允许的最大尝试次数。 |
| Register rate limit window (seconds) | 注册限流使用的时间窗口（秒）。 |
| Registration key | 受控注册流程使用的可选共享密钥。 |
| Server identity salt | 服务器身份推导逻辑可选的盐值覆盖项。 |
| Admin IP allowlist | 管理端点保护使用的 IP 白名单（逗号分隔）。 |
| Treat this server as a public hub | 将服务器标记为公开 hub 模式。 |
| PIN sync auto default | 设置 PIN 自动同步的默认策略。 |
| Force manual PIN sync | 强制 PIN 同步改为手动而非自动。 |
| Push queue workers (tools GUI) | 推送队列处理并发 worker 数。 |
| Push queue max size (tools GUI) | 推送队列最大缓冲大小。 |
| Presence backend | Presence 状态存储后端类型（memory/redis）。 |
| Presence Redis URL | 当 Presence backend 为 redis 时使用的 Redis 连接 URL。 |
| Fanout backend | Fanout 分发存储后端类型（memory/redis）。 |
| Fanout Redis URL | 当 Fanout backend 为 redis 时使用的 Redis 连接 URL。 |
| Region policy mode | 区域路由策略模式（auto/manual）。 |
| Default region | 区域策略默认区域标识。 |
| China FCM policy | 中国相关路由场景下的 FCM 处理策略。 |
| Push Hub License (Advanced, custom backend GUI) | Advanced Mode 中用于购买/确认 Push Hub 功能的授权字符串。 |
| LEGACY_PUBLIC_URL | 旧版后端 docs 打开辅助功能使用的公开 URL。 |
