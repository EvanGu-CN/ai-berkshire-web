# 服务器配置、验证与直连发布

标准流程：**本地检查 → 提交并推送 main → 用本地配置直连部署 → 核对部署结果**。GitHub 保存源码和版本记录，不需要 PR、GitHub Actions、服务器 Secrets 或在线运维工作流。所有命令从仓库根目录执行。

## 新电脑接管

准备 Git、Python 3.10+、Node.js 22+。克隆仓库后创建本机虚拟环境；不要复制旧电脑的 `.venv`。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ai-berkshire-web/requirements-dev.txt -r ops/requirements.txt
# 只在配置尚不存在时创建示例副本
if (!(Test-Path .env.local)) { Copy-Item .env.example .env.local }
```

已有 `.venv` 时可直接安装依赖。Linux/macOS 将 `.\.venv\Scripts\python.exe` 替换为 `./.venv/bin/python`。先确认用于创建虚拟环境的解释器版本符合要求。

从可信的本机备份安全迁入 `.env.local`，或者按示例填写。私钥需单独迁移，文件路径可用绝对路径、`~`，或相对于配置文件的路径。真实配置和私钥不进 Git，也不要在聊天和日志中打印。Linux 使用 `chmod 600 .env.local`；Windows 限制为自己的账户和系统可访问。克隆代码不会带来 SSH 凭据或生产数据。

## 三种配置与数据位置

| 内容 | 位置与用途 | 如何维护 |
|---|---|---|
| 本机 SSH 运维配置 | 根 `.env.local`，或 `--env-file` 指定文件 | 仅运维工具读取，不上传、不入库 |
| 服务器应用运行配置 | `VC_REMOTE_ENV`，现有安装通常为 `/etc/ai-berkshire-web.env` | systemd 读取；部署仅检查存在，不覆盖 |
| 各用户模型配置、账号和历史 | `VC_DATA_DIR/app.db` | 应用按用户隔离保存；发布保留并检查连续性 |

`.env.local` 不是网站的环境文件，不用于给用户设置 API Key 或重置管理员密码。部署配置中的 `VC_DATA_DIR` 是**服务器目录**；本地启动网站时，应用自身的 `VC_DATA_DIR` 才表示本机数据目录。当前网站启动器不自动加载根 `.env.local`。

| 配置项 | 含义/默认值 |
|---|---|
| `VC_SSH_HOST / VC_SSH_PORT / VC_SSH_USER` | 目标主机、端口（默认 22）、SSH 用户 |
| `VC_SSH_PASSWORD / VC_SSH_KEY_FILE` | 选一种认证方式填写；另一项留空 |
| `VC_SSH_KNOWN_HOSTS` | 本机主机公钥缓存，默认配置文件旁的 `.local/known_hosts` |
| `VC_SSH_HOST_KEY_SHA256` | 可选主机指纹，填写从可信渠道获得的 `SHA256:...` |
| `VC_REMOTE_DIR` | 服务器部署根目录，默认 `/opt/ai-berkshire-web` |
| `VC_DATA_DIR` | 服务器持久数据目录，默认 `/var/lib/ai-berkshire-web` |
| `VC_BACKUP_DIR` | 服务器部署备份目录，默认 `/var/backups/ai-berkshire-web` |
| `VC_SERVICE` | 现有 systemd 服务，默认 `ai-berkshire-web.service` |
| `VC_REMOTE_ENV` | 现有应用运行配置，默认 `/etc/ai-berkshire-web.env` |
| `VC_HEALTH_URL` | 从服务器访问的就绪接口，默认 `http://127.0.0.1:8765/api/auth/policy` |
| `VC_PUBLIC_URL` | 从本机访问的公开接口完整地址，也使用 `/api/auth/policy`；留空会跳过公网检查 |

应用运行示例另见根 `.env.app.example`，不能覆盖本机 `.env.local`。`VC_PLATFORM_CONFIG` 可指向部署自己的会员/额度/时区策略；`VC_FRAMEWORK_DIR` 指向外部技能框架。新库默认管理员名称为 `admin`，模型配置默认演示模式且不预填地址、模型或密钥。升级旧库保留已有管理员 ID、会员标识、额度和用户模型连接。

环境变量优先于配置文件，包括空值覆盖；没有在文件中填写的选项使用默认值。密码包含空格、`#` 或反斜杠时，可使用 JSON 双引号字符串；文件不是 shell 脚本，不执行命令或变量替换。私钥路径支持系统环境变量展开。`--env-file` 放在子命令前：

```powershell
.\.venv\Scripts\python.exe -m ops.remote --env-file .local/staging.env status
```

首次连接将主机公钥保存在本地缓存；之后密钥变更会拒绝连接。配置了指纹时，首次连接也需匹配。遇到主机指纹变化，先核对服务器身份，勿直接清空缓存。

## 本地检查

```powershell
.\.venv\Scripts\python.exe -m ops.check
# Node 不在 PATH 时
.\.venv\Scripts\python.exe -m ops.check --node "C:\path\to\node.exe"
# 明确只检查 Python（不能视为完成前端验证）
.\.venv\Scripts\python.exe -m ops.check --python-only
```

默认运行应用及运维 pytest、Python 编译、JavaScript 语法及单元测试、`git diff --check`，任一失败即退出失败。该入口不读取 `.env.local`、不连 SSH、不调用真实模型，测试数据放在忽略入库的 `.local/checks/` 临时目录。Windows 会明确跳过 POSIX 发布协议测试；修改发布脚本时需另外在 Linux/WSL 环境运行 `python -m pytest ops/tests/test_release_script.py -q`。也可在独立 Linux 测试目录中运行，测试使用模拟服务和合成数据库，不接触生产目录。Windows 检查通过不代表这些 Linux 场景已执行。

界面改动额外执行隔离的浏览器回归。以下版本沿用项目已验证的 Playwright 工具，安装目录不入库：

```powershell
npm install --prefix .local/browser --cache .local/npm-cache playwright@1.51.1
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD .local/browser-cache
node .local/browser/node_modules/playwright/cli.js install chromium webkit
$pwModule = (Resolve-Path .local/browser/node_modules/playwright).Path
.\.venv\Scripts\python.exe -m ops.check --browser --playwright-module $pwModule
```

浏览器检查使用 `tests/smoke_server.py` 的隔离账号、合成资料和演示模型，检查桌面/手机页面、阅读器、报告导入和空研究记忆索引下的观点时间线。占用本机 8765 端口，发现端口已在使用会拒绝运行；测试完成或失败后关闭自行启动的进程。截图与测试服务日志保存在 `.local/browser-results/`。Linux 缺浏览器系统依赖时，按本机权限安装 Playwright 的系统依赖。此处不使用生产地址，也不消费真实模型额度。

Linux/macOS 安装浏览器时使用 `export PLAYWRIGHT_BROWSERS_PATH="$PWD/.local/browser-cache"`，检查时传入 `--playwright-module "$PWD/.local/browser/node_modules/playwright"`；其他 Python 命令使用该系统的 `.venv/bin/python`。

## 生成公开演示截图

安装上节的 Playwright 和 Chromium 后，从仓库根目录运行：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD .local/browser-cache
$pwModule = (Resolve-Path .local/browser/node_modules/playwright).Path
.\.venv\Scripts\python.exe -m ops.showcase --playwright-module $pwModule
```

工具在 `127.0.0.1:8766` 启动 `tests/showcase_server.py` 的临时应用；端口被占用时拒绝运行。它不读取 `.env.local`，移除继承的应用配置，单独创建临时数据库、虚构公司与合成资料，所有模型密钥均为空。浏览器只允许访问本机截图服务，禁止发起研究/模型操作，并校验临时服务身份。生成后关闭进程并清理临时数据。

默认输出在忽略的 `.local/showcase-output/`，包括 7 张 PNG、检查摘要及本地服务日志。`--output <目录>` 可另选截图输出目录。只将逐张检查后的 `workbench.png`、`reports.png`、`reader.png`、`import.png`、`timeline.png`、`settings.png`、`mobile.png` 放入 `docs/screenshots/`，同步 [README 界面预览](../README.md#界面预览)；不要复制日志、临时数据库或检查摘要到公开图片目录。

公开导出工具只放行上述固定名称的演示图片；其他截图默认不导出。文字扫描不能识别图像中的敏感内容，发布前必须人工查看全部图片，确认没有真实研报、用户、地址或模型密钥。演示种子仅由截图命令加载，正常安装不加载。

修改 `ai-berkshire-web/static/brand/` 中的 SVG 品牌源图后，用上述浏览器环境运行 `node ops/build-brand-icons.cjs <Playwright 模块绝对路径>`，重新生成 16/32/48px PNG、多尺寸 ICO 和 180px 移动书签图标，再一起提交；正常应用启动与发布不需要运行此生成器。浏览器回归会核对站内标志、图标声明和图像解码。

## 日常诊断

```powershell
.\.venv\Scripts\python.exe -m ops.remote --help
.\.venv\Scripts\python.exe -m ops.remote status
.\.venv\Scripts\python.exe -m ops.remote logs --lines 100 --since "30 minutes ago"
.\.venv\Scripts\python.exe -m ops.remote exec "systemctl is-active ai-berkshire-web.service"
.\.venv\Scripts\python.exe -m ops.remote exec --script .local/diagnose.sh --timeout 300
```

`status` 检查服务及配置的健康地址，并显示当前发布目录。`logs` 读取指定服务的 journal 到本机；`exec` 可以执行任意远程命令，应明确命令范围，不输出环境文件或密钥。原始日志可能含用户数据和供应商响应，只在本地按需查看。真实模型复测不是发布的自动步骤；按用户授权的对象、次数和费用边界另行执行。

## 提交与发布

先检查本地改动，选定本次任务的文件；保留其他任务的未提交内容。确认当前分支为 main，与远端一致；远端有新提交时先协调合并，不强推。

```powershell
git status --short --branch
git fetch origin
# 查看尚未同步的提交；有输出时先处理远端变更
git log --oneline HEAD..origin/main
.\.venv\Scripts\python.exe -m ops.check
git add <本次修改的文件>
git diff --cached --check
git commit -m "描述本次改动"
git push origin main
# 前一步必须成功，再发布刚刚推送的完整提交
$releaseCommit = git rev-parse HEAD
.\.venv\Scripts\python.exe -m ops.remote deploy --ref $releaseCommit
.\.venv\Scripts\python.exe -m ops.remote status
```

代码块中的 `<...>` 是占位符，执行前替换为实际文件或提交。每步失败先停止并处理原因。推送不会自动部署；`deploy` 也不会自动创建提交或推送。`--ref` 默认 HEAD，建议明确传入已检查、已推送的提交。未提交文件不会打包，其他分支的内容也不会自动合并进来。发布只包含该提交下的 `ai-berkshire-web/`、`ops/`、`update-ai-berkshire.py`。

## 发布期间自动完成的保护

1. 获取部署锁、核对现有安装、检查上传包 SHA256。复用共享研究框架，先准备新版本目录、虚拟环境和依赖。
2. 检查是否有进行中的研究任务；有任务就停止本次发布，等完成后再运行。工具不主动取消用户研究。
3. 短暂停止服务，将整个持久数据目录备份到独立目录，生成服务器内的数据行哈希基线，再切换 `current`。
4. 启动服务并检查就绪接口；验证既有账号、会话、模型配置、历史、账户规则、用量和邀请数据仍保留。允许新增行和列；不允许基线中的既有值消失或被替换。
5. 本机检查公开接口。配置留空时不会执行公网检查，不能把跳过写成通过。

部署输出包含 `backup_dir=...`、`deployed_commit=...`、`data_continuity=passed`、`local_health=passed`，以及配置公网地址时的 `public_health=passed`。保留实际提交、备份位置和最终状态作为交付记录。相同提交已经在线时，只核对健康并清理上传包，不重复重启、备份或生成连续性基线。

健康检查证明服务可运行、接口可访问，不等同于所有业务或真实模型研究均已验收。连续性比较覆盖既有核心数据库记录；全目录备份另外保留 `memory.db` 和缓存等文件，不对每个缓存文件逐行比较。

时间线的派生规则结果单独保存在持久目录 `timeline_cache.db`，不占用 `memory.db` 的全文索引锁，常规升级沿用未失效的结果；全目录备份会包含该缓存，但无需将其作为核心用户表逐行验证。源报告或规则变化会自动失效，缓存故障不会覆盖或删除原报告。入口 HTML 每次重新验证，静态资源 URL 由内容指纹自动生成，无需手动要求用户清空脚本缓存。

本地批量导入的报告保存在持久数据库 `imported_reports` 表中，包含正文、元数据、导入者及内容哈希；部署连续性验证也覆盖该表。首次升级自动新增表，既有账号、模型配置、历史和额度不变。报告正文不随代码包或 Git 推送，完整数据备份与迁移会保留导入表。

导入按“账号 + 规范化正文哈希”跨路径去重，同公司、日期、类型但正文不同的多份报告可独立保存。应用检查与数据库插入触发器共同阻止新增相同正文，内容查询有专用索引。历史文件版本索引继续兼容已有数据；旧库升级、失败回滚及备份恢复保留原行、ID、元数据和正文，不自动删除历史记录。部署切换前仍完整备份并生成连续性基线，升级后验证既有导入行。

## 失败与恢复

| 情况 | 处理 |
|---|---|
| 连接或配置失败 | 核对 SSH 配置、私钥文件和主机指纹；已有网站不受本次连接失败影响 |
| 部署锁或研究任务占用 | 等现有任务结束再发布，不强行终止 |
| 准备新目录或依赖失败 | 保留原服务；根据提示定向检查网络、磁盘和依赖 |
| 已停服后的备份、切换、健康或连续性失败 | 工具尝试恢复原代码目录并重启；返回失败，务必再运行 status 核对 |
| 仅本机公网检查失败 | 新版本可能已正常在线；先核对本机网络、公开地址与入口，工具不会因此自动还原数据库或切换代码 |

代码回退与数据恢复不同：工具不会自动用旧数据库覆盖现有数据。需要恢复数据时，先明确目标备份、恢复范围和期间新数据的处理方式，并取得授权。包含 `ops/deploy.sh` 的旧提交可以通过 `deploy --ref <旧提交>` 重新发布；前提是旧代码仍兼容当前数据库。更早版本保留在 `releases/`，需单独核对后恢复，不能直接作为新协议的发布包。

## 服务器目录与维护边界

默认布局如下，实际以本地配置为准：

```text
/opt/ai-berkshire-web/
  current -> releases/<commit>-<随机后缀>/
  releases/<commit>-<随机后缀>/
    REVISION                         完整 Git 提交
    ai-berkshire-web/                 应用源码
    ops/                             此提交的运维工具
    .venv/                           此版本运行依赖
    ai-berkshire -> /opt/ai-berkshire-web/shared/ai-berkshire
  shared/ai-berkshire/                已有共享研究框架
/var/lib/ai-berkshire-web/                持久数据，不在源码发布目录中
/var/backups/ai-berkshire-web/<commit>-<随机后缀>/
  data.tar.gz                        停服后的全数据目录备份
  continuity.json                    核心数据的哈希基线
/etc/ai-berkshire-web.env                 已有 systemd 运行配置
```

备份和旧版本不自动删除，定期确认磁盘容量后按明确范围维护。备份含账号及密钥等敏感数据，只保留在服务器受限目录或受控离线位置。网页或命令行的 ZIP 数据迁移是另一种导出/导入机制，与部署产生的 `data.tar.gz` 不能混用。

部署保留原运行配置、Nginx、其他服务和共享框架，不更新上游框架、不设置管理员密码。上游同步、全新服务器安装、运行环境或入口变更分别处理；不要在生产 `current` 中运行本地整目录替换脚本。服务器应已具备 Python venv、curl、flock、tar、systemd、现有应用服务及相应 SSH 操作权限。

官方框架的内容更新使用网页“报告库 → 同步官方仓库”。同步保留发布链接及共享目录，在共享目录内暂存新文件并回滚失败的切换；应用用户只需对 `shared/ai-berkshire/` 及其内容有写权限，不需要写入由 root 管理的 `shared/` 父目录。若状态提示恢复失败，先保留其给出的 `.upstream-sync-*/previous/` 备份并定向检查，不要删除备份目录或运行整目录替换脚本。
