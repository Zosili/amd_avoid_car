# 协作规则（两人）

> 仓库是 **private**：只有两名成员有权限。任何「先公开再删」的操作都不要做——历史里删不掉。

## 1. 谁能读写

| 角色 | 权限 | 说明 |
|---|---|---|
| 仓库所有者 | Admin | 建库、加人、改设置、合并 PR |
| 队友 | Write（Collaborator） | 推分支、开 PR、合并自己的 PR（建议仍走 review） |
| 其他人 | 无 | 不公开、不加第三方 App、不装会读代码的 CI 机器人 |

**加人**（所有者执行，三选一）：

```bash
# A. 知道对方 GitHub 用户名（推荐，一条命令）
gh api -X PUT repos/Zosili/amd_avoid_car/collaborators/<用户名> -f permission=push
#    只读改成 -f permission=pull；写权限给 push

# B. 只想让对方看（read-only）
gh api -X PUT repos/Zosili/amd_avoid_car/collaborators/<用户名> -f permission=pull
```

**C. 只有邮箱 / 对方还没账号**：打开 `https://github.com/Zosili/amd_avoid_car/settings/access` →
`Add people` → 输入邮箱 → 邀请邮件里带接受链接（对方按提示注册即可）。

**接受邀请**：被邀请人登录后打开 `https://github.com/Zosili/amd_avoid_car/invitations` 点接受。
（GitHub 的协作者邀请会过期，过期就重发；这个页面任何时候都能看有没有待接受的邀请。）

> ⚠️ **GitHub 没有「私有库公开邀请链接」**：私有仓库只能按「用户名 / 邮箱」点名加人，不存在谁点谁能进的链接
> （[GitHub 文档](https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-personal-account-on-github/managing-access-to-your-personal-repositories/inviting-collaborators-to-a-personal-repository)、
> [Stack Overflow](https://stackoverflow.com/questions/31264165/can-i-share-my-private-github-repository-by-link)、
> [社区讨论](https://github.com/orgs/community/discussions/171917)）。
> 需要「一个链接给一群人」只能靠组织/团队（Organization + Team），或个人库逐个点名。

队友本地首次拉取（任选其一）：

```bash
git clone https://github.com/Zosili/amd_avoid_car.git      # 会弹浏览器授权
git clone git@github.com:Zosili/amd_avoid_car.git          # 已配 SSH key 的话
```

> 若账号开了 2FA，HTTPS 推送要用 **Personal Access Token**（scope 只需 `repo`，推 CI 文件才需要 `workflow`），别把 token 写进 remote URL 或任何文件。
> 本机（所有者）已在这个仓库设了 `http.proxy=http://127.0.0.1:7892`：直连 github.com 会超时，`api.github.com` 直连可用。

### 私有性自查（三分钟，任一条不符就说明配置被改过）

```bash
# ① 有权限的人看：必须 true / private
gh api repos/Zosili/amd_avoid_car --jq '{private, visibility}'

# ② 匿名看网页：私密仓库返回 404（不是 200/302）
curl -s -o /dev/null -w '%{http_code}\n' https://github.com/Zosili/amd_avoid_car

# ③ 匿名读仓库：必须被要求认证（fatal: could not read Username）
git -c credential.helper= ls-remote https://github.com/Zosili/amd_avoid_car.git
```

另外确认这几项没被打开：`Settings → General → Danger Zone`（不要 `Change visibility`）、
`Settings → Pages`（私有库默认无站点）、`Settings → Access → 没有多余协作者`、无 Deploy key、无 GitHub App。

> 注：`api.github.com` 对**未认证**请求有共享 IP 的限流（403 rate limit exceeded），
> 443 的 404/认证失败才是判断私有性的可靠信号。

## 2. 分支与提交流程

```
main            永远可复现（CI 通过 + 至少 1 人 review）
feat/<short>    新功能       例：feat/vision-7hz
fix/<short>     修 bug       例：fix/radar-zero-offset
docs/<short>    只改文档
exp/<short>     实验性（可以不合，允许不优雅）
```

1. `git switch -c feat/xxx main`
2. 小步提交，一个提交只做一件事
3. 推送后开 PR，PR 描述写清：**改了什么 / 为什么 / 怎么验证的（贴命令与输出）**
4. 另一人 review（重点看契约与阈值），CI 绿了再合并
5. 合并方式用 **Squash and merge**，保持 `main` 线性

**提交信息**（Conventional Commits 简版）：

```
feat(radar): 支持 D6 起始包 LSN=1
fix(fusion): R2 侧向转向连续两帧才执行
docs(interface): 补 5000 端口 TCP/UDP 冲突说明
metrics: 补决策周期复算脚本
```

## 3. 什么改动必须两人一起看

| 文件 | 原因 |
|---|---|
| `host/app/car/protocol.py` | 消息 schema、阈值 `CFG`、动作集合 —— 改了三端全受影响 |
| `host/app/integration/contract.py` | steering→PWM 映射、视觉归一化、频率契约（`CONTRACT_VERSION`） |
| `host/app/car/radar/d6_protocol.py` | 解帧公式与扇区口径（错一位全是假距离） |
| `fpga/src/motor/motor_axi.v` | 寄存器映射与看门狗（PS 侧 `run_on_board.py` 必须同步） |
| `metrics/metrics.csv` | 对外报数，改口径等于改结论 |

规则：**改契约必须 `CONTRACT_VERSION += 1`**，并在 PR 里写「破坏性变更 + 影响面 + 已跑的验证命令」。

## 4. 不要提交的东西

| 类型 | 处理 |
|---|---|
| 模型（`*.pt/*.onnx`）、bit/xsa、SD 镜像、雷达原始录包 | 走 `.gitignore`；必须入库的二进制用 **Git LFS**（`git lfs track "*.bit"`，已在 `.gitattributes` 配好） |
| 个人路径、token、密码、内网 IP 清单 | 不要写进代码；配置用环境变量或 `*.local` 文件（已忽略） |
| 调试用的临时日志 | 放 `metrics/logs/` 且只留**能复算结论**的样本 |

单文件 > 20 MB 会被 CI 拦下（`tools/check_repo_layout.py`）。

## 5. 命名（指南 3.2.5 硬要求）

目录名、文件名只用 **小写字母、数字、`.`、`_`、`-`**；中文只出现在**文件正文**里。
标准文件名保持原样：`README.md`、`MODEL.md`、`CONTRIBUTING.md`。

提交前本地自查（CI 也会跑）：

```bash
python tools/check_repo_layout.py     # 0 error 才算干净
python -m pytest host/app/car -q      # 32 passed
```

## 6. 提交前的最小验证清单

```bash
python tools/check_repo_layout.py                    # 命名/结构/表头/体积
cd host/app && python -m pytest car -q               # 决策 + 雷达解析单测
cd host/app/integration && python contract.py        # 契约自检
cd host/app && bash joint/_run_it.sh person_left     # 三端链路（无硬件）
python metrics/scripts/latency_stats.py              # 指标可复算
```

四条都过再推。**改 FPGA 的 RTL 时另加**：重新综合实现并更新 `fpga/report/*.rpt`（报告是快照，不更新等于说谎）。

## 7. 私有仓库的边界

- 不建 public fork，不把代码贴进公开 issue / 论坛 / 比赛群。
- 提交物（3.2.5 打包）另走赛事通道，**不要把赛事账号密码写进仓库**。
- 换机器/加第三个人：由所有者加 collaborator，**不要共享同一个账号**（否则提交历史与责任分不清）。
