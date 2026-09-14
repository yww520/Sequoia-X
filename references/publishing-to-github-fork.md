# 把本 skill 推送到用户 fork（发布流程）

场景：用户说「把修改后的项目推送到我的 GitHub」。本项目本体在
`~/.hermes/skills/research/sequoia-x/`，**不是 git 仓库**（当初是解包/复制进来的，
没有 `.git`）。上游是 `sngyai/Sequoia-X`，用户 fork 是 `yww520/Sequoia-X`。

以下是实测跑通（直到认证那一步）的完整流程和踩过的坑。

---

## 一、先探认证 —— 决定后面能不能推

**在花时间构建仓库之前先探认证**，否则会做一堆无用功。三个地方都要查：

```bash
gh auth status                          # 经常是坏 token
gh api user --jq .login                 # 401 就是死了
cat ~/.config/gh/hosts.yml | sed 's/oauth_token:.*/oauth_token: [REDACTED]/'
git config --global --list              # user.name/email
ls -la ~/.ssh/ ~/.git-credentials
cat ~/.ssh/config
```

### ⚠️ 最大的坑：deploy key 是按仓库授权的

`~/.ssh/` 下有个 key，`ssh -T git@github.com` 返回的却是**别的仓库名**：

```
$ ssh -T git@github.com
Hi yww520/lay-s-buffet-system! You've successfully authenticated...
```

**这句话必须读完、看懂。** `Hi <owner>/<repo>!` 中的 `<owner>/<repo>` 是**该 key 被授权
的那一个仓库**，不是用户名。说明它是 **deploy key（按仓库授权）**，不是账号级 key。
拿它推另一个仓库会被拒：

```
$ git push origin HEAD:refs/heads/__probe
ERROR: Permission to yww520/Sequoia-X.git denied to deploy key
```

→ **永远先做一次「探针推送」验证真实写权限**，不要因为 `ssh -T` 说 "successfully
authenticated" 就以为能推：

```bash
cd /tmp && rm -rf push_probe && mkdir push_probe && cd push_probe
git init -q && echo t > t.txt && git add t.txt
git -c user.email=u@e -c user.name=u commit -qm probe
git remote add origin git@github.com:<owner>/<target-repo>.git
git push origin HEAD:refs/heads/__permission_probe    # 通了记得删掉这个分支
```

### 认证失效时的处置

- **gh token 失效** → 不要反复重试，直接告诉用户："token 已过期，需要新的 PAT（scope 勾 `repo`）"。
- **ssh 是别的仓库的 deploy key** → 给出两个选项让用户选：
  (A) 生成新 PAT；(B) 把现有 pub key 加到目标仓库的 Deploy keys 并勾 **Allow write access**。
  同时给出 `~/.ssh/<key>.pub` 的内容（`cat` 出来即可，公钥可安全展示），方便用户粘贴。
- **公开仓库的 HTTPS 只读克隆不需要认证** —— 所以「能 clone 上游」**不能**证明「有推送权限」。

---

## 二、构建仓库（认证探明后再做，或并行）

### 用 upstream 的历史做基底，避免无关历史

直接 `git init` 会得到一个与上游**无共同祖先**的仓库，push 后 diff 会变成"整个仓库重写"。
正确做法：先把上游 `master` 拉下来，再把改动 graft 到它上面。

```bash
cd /tmp && rm -rf build && git clone --depth 1 <upstream-https-url> build
cd build && rm -rf .git && git init -q -b main
# 复制本 skill 的改动进来（见下）
```

或者保留上游历史（更干净，推荐）：

```bash
cd /tmp/build && git fetch origin   # 拿到 origin/master
git reset --soft $(git rev-parse origin/master)   # 历史基点对齐，改动留在 index
git commit -m "..."                 # 变成 clean 的单次提交，父节点=上游 HEAD
```

验证 graft 成功：`git log --oneline -2` 应看到「你的提交」的**下一行**就是上游的 commit。

### 复制文件：注意权限位

skill 目录里的文件常是 `-rw-------`（600，Hermes 写入的默认权限）。复制后要放宽，
否则别人 clone 下来读不了：

```bash
chmod 644 *.py *.md; chmod 644 references/* scripts/*
```

---

## 三、🔴 必须检查的坑：`.gitignore` 的 `data/` 会误伤包目录

**本次实际踩到，且很隐蔽。**

本 skill 的 `.gitignore` 里有 `data/`（想忽略运行时的 580MB SQLite）。但项目包内部
**也有一个 `sequoia_x/data/` 目录**（数据引擎代码）。未加根锚定的 `data/` 会**同时匹配**
`./data/` 和 `./sequoia_x/data/`，导致：

- `git add -A` 静默跳过 `sequoia_x/data/engine.py`
- 提交里出现 **`sequoia_x/data/engine.py | 333 -------`（整文件被删）**
- 危险信号：diff 里出现**纯删除**。**看到删除就要查，别当正常。**

修法 —— 加上根锚定 `/`：

```gitignore
/data/*.db      # 只忽略根目录的库文件，不碰 sequoia_x/data/
```

**通用规则：`.gitignore` 里所有目录名都加根锚定 `/`，除非你确信要递归匹配。**

### 提交前必做的三道自检

```bash
# 1. 审视 diff —— 有没有意外删除？
git show --stat HEAD | tail -30
git diff --stat $(git rev-parse origin/master) HEAD | grep -E '^-|delete'

# 2. 确认关键文件真的入库了
git ls-files | grep 'sequoia_x/data'        # 应有两个文件

# 3. 待添加清单里没有敏感文件
git add -A --dry-run | grep -E '\.env|\.venv|\.db|credential'
```

### 敏感信息扫描

推之前对**所有要提交的文件**扫一遍（本 skill 的脚本里出现过 `https://open.feishu.cn/placeholder`
这样的占位 webhook，是安全的；真 webhook 是密钥）：

```bash
grep -nE '(sk-[A-Za-z0-9]{10,}|[0-9a-f]{32,}|https://open\.feishu\.cn/[^ ")]+|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})' \
  *.py *.md references/* scripts/*
```

要求：`.env`、`data/*.db`、`.venv/`、`__pycache__/` 必须都在 `.gitignore` 里且
**不出现在 `git add -A --dry-run` 的输出中**。

---

## 四、README 处置 —— 先问用户

本 skill 的改动是"在原项目上做大改造"，直接改 `README.md` 会把上游的项目介绍冲掉。
**做完后主动告知用户**："我改了 README（顶部插入改造说明）；若你想让 README 与上游
保持一致、只把说明放进 SKILL.md，告诉我。" —— 让用户决定，别擅自定。

同理，`SKILL.md` 是 Hermes skill 的元文件，放进 GitHub 仓库没问题（可作 skill 分发），
但要说明它是给 Hermes 用的。

---

## 五、推不上去时怎么汇报

**只报事实，不编造成功。** 明确区分「已完成」和「阻塞」：

- ✅ 已构建+已提交（给 commit hash、文件统计）
- ❌ 阻塞在认证（列出**试过的每条路径及其具体报错**）
- 给出 2 个可选解法，让用户挑

不要因为推送失败就把整个任务说成失败 —— 仓库已构建好是实打实的成果，
`/tmp/build` 里的提交在拿到凭据后可以直接 push。

---

## 快速清单

1. 探认证（`gh auth status` / `~/.ssh/config` / 探针推送）→ 拿不到就**先问用户要 PAT**，别硬做
2. clone upstream 做基底 → `reset --soft origin/master` → 让改动 graft 成单次提交
3. 复制文件 + `chmod 644`
4. **收紧 `.gitignore`（目录名加 `/` 锚定）** ← 最容易翻车
5. 自检：diff 无意外删除 / 关键文件已入库 / 无敏感文件
6. `git show --stat HEAD` 复核 → commit（用用户的 name/email）
7. push；失败则明确报阻塞 + 给选项
