# 无旧历史的公开发布

推荐保留原运维仓库为私有，新建空仓库发布审核后的源码快照。不要直接把旧仓库改为公开，不要使用镜像推送、复制 `.git` 或推送旧标签。

## 生成公开源码

先检查并提交应当发布的源码。导出工具只读取指定提交中的允许文件，拒绝运行数据、研报目录、私密环境文件、符号链接和嵌套仓库，并检查常见密钥格式与个人主目录。

```powershell
# 可先只检查；private-env 与 deny-file 均可省略，内容不会写入公开包
.\.venv\Scripts\python.exe -m ops.public_release --ref HEAD
# 个人标识数组可保存在忽略的 .local/private-identifiers.json 中
.\.venv\Scripts\python.exe -m ops.public_release --ref HEAD `
  --private-env .env.local --deny-file .local/private-identifiers.json `
  --output .local/public-release/ai-berkshire-web --init-git
```

目标目录和同名 ZIP 必须不存在。生成结果包含：普通源码、许可证、第三方声明、文件哈希清单；独立目录中的 Git 仓库只有一个新根提交，不继承旧作者邮箱、远端、分支、标签、Reflog 或对象。ZIP 不含 `.git`。

检查源码目录和 ZIP 后，在生成目录中运行项目测试，尤其是新库零报告、零私人模型连接与权限隔离。检查工具属于辅助检查，无法证明所有格式或所有上下文中的敏感内容都已识别；公开前仍需人工查看清单与必要源码。

## 推送全新仓库

在 GitHub 创建一个新的空仓库，先保持私有，不预先创建 README、许可证或 `.gitignore`。进入**新生成的目录**后执行：

```bash
git rev-list --count --all
# 必须为 1
git rev-list --parents -n 1 HEAD
# 只有当前提交，无父提交
git remote -v
# 初始应为空
git remote add origin https://github.com/YOUR_ORG/YOUR_REPOSITORY.git
git push -u origin main
```

在网页检查新仓库只有一条初始提交，目录无数据、日志和备份，再决定改为公开。后续只向这个干净仓库推送公开源码；不要把私有运维分支或标签合并过来。使用公开用户名或 GitHub 隐私邮箱提交后续修改。

## 旧历史与敏感值

新仓库可以从零开始，但不能撤销旧仓库、旧克隆、Fork、PR 引用、Actions 日志或缓存中的既有暴露。若确认历史包含仍有效的凭据，需要在对应服务中撤销或轮换；清理 Git 历史不能使已泄漏凭据失效。

若必须保留原仓库地址，应另行规划 `git-filter-repo` 与全部引用、缓存、协作者副本清理，不能仅建立孤儿分支后强推便认为已经安全。当前导出工具不修改原仓库历史或可见性。

参考 GitHub 官方说明：[移除敏感数据](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)、[仓库可见性](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)。
