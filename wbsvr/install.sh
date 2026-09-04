#!/bin/sh
# wbsvr 安装脚本 —— 建专用账户、装 wbsvrd、放 sudoers 允许清单、逐条自检。
#
# 用法：  sh wbsvr/install.sh          （会在需要时自己提权）
# 卸载：  见文末注释，四条 rm/dscl，刻意不做 --uninstall（用一次的东西不值一个分支）
#
# 这个脚本改的四样东西每一样配错都会让整套隔离静默归零，所以每一步装完立刻验一遍，
# 而不是装完统一说「成功」。可重复跑。
set -eu

BIN=/usr/local/libexec/wbsvrd
STORE=/var/lib/wbsvr
SUDOERS=/etc/sudoers.d/wbsvr
SVC=wbsvr

SRC=$(cd "$(dirname "$0")" && pwd)
STAGE="$SRC/.wbsvrd.stage"

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
say() { printf '  %s\n' "$*"; }
ok()  { printf '  [ok]   %s\n' "$*"; }
bad() { printf '  [FAIL] %s\n' "$*"; FAILED=$((FAILED + 1)); }
warn(){ printf '  [warn] %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 未提权：先以普通用户构建（go 工具链在用户 PATH 里，root 的 PATH 常常没有），
# 再 exec sudo 跑同一个脚本。构建产物走固定路径而不是环境变量 —— sudo 的 env_reset
# 会把变量清掉，而放宽它正是这套设计要避免的那类「配对了才安全」。
# ---------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    command -v go >/dev/null 2>&1 || die "需要 Go 工具链来构建 wbsvrd（brew install go / apt install golang）"
    printf '构建 wbsvrd...\n'
    (cd "$SRC" && go test ./... >/dev/null) || die "wbsvrd 测试没过，不装"
    (cd "$SRC" && go build -trimpath -o "$STAGE" .) || die "构建失败"
    ok "$STAGE"
    printf '\n接下来需要 sudo（建服务账户、写 /etc/sudoers.d）。\n'
    exec sudo "$0" "$@"
fi

AGENT_USER=${SUDO_USER:-}
[ -n "$AGENT_USER" ] || die "拿不到 agent 账户名。别直接以 root 跑，用普通用户跑（脚本会自己提权）"
case "$AGENT_USER" in
    *[!A-Za-z0-9._-]*) die "agent 账户名含特殊字符，拒绝写进 sudoers：$AGENT_USER" ;;
esac
[ -f "$STAGE" ] || die "找不到构建产物 $STAGE —— 用普通用户跑本脚本，别直接 sudo"

case "$(uname -s)" in
    Darwin) PLATFORM=darwin; ROOT_GROUP=wheel ;;
    Linux)  PLATFORM=linux;  ROOT_GROUP=root  ;;
    *) die "只支持 macOS 与 Linux" ;;
esac

printf '\nagent 账户：%s    平台：%s\n\n' "$AGENT_USER" "$PLATFORM"

# ---------------------------------------------------------------------------
# 1. 服务账户
# ---------------------------------------------------------------------------
printf '1/4 服务账户 %s\n' "$SVC"
if id "$SVC" >/dev/null 2>&1; then
    ok "已存在，跳过"
elif [ "$PLATFORM" = darwin ]; then
    # 服务账户用 200-400 的 UID：小于 500 的不出现在登录窗口。
    taken=$(dscl . -list /Users UniqueID | awk '{print $2}'; dscl . -list /Groups PrimaryGroupID | awk '{print $2}')
    id_n=300
    while printf '%s\n' "$taken" | grep -qx "$id_n"; do id_n=$((id_n + 1)); done
    [ "$id_n" -lt 400 ] || die "200-400 段没有空闲的服务账户 ID 了"
    dscl . -create "/Groups/$SVC" PrimaryGroupID "$id_n"
    dscl . -create "/Users/$SVC"
    dscl . -create "/Users/$SVC" RealName "workbench contract host"
    dscl . -create "/Users/$SVC" UniqueID "$id_n"
    dscl . -create "/Users/$SVC" PrimaryGroupID "$id_n"
    dscl . -create "/Users/$SVC" NFSHomeDirectory /var/empty
    # 没有可用 shell 是这个账户唯一的用途保证：它只被 sudo 拿来 exec wbsvrd。
    dscl . -create "/Users/$SVC" UserShell /usr/bin/false
    ok "建好，uid=$id_n，shell=/usr/bin/false"
else
    nologin=/bin/false
    for c in /usr/sbin/nologin /sbin/nologin; do [ -x "$c" ] && nologin=$c && break; done
    useradd --system --no-create-home --home-dir "$STORE" --shell "$nologin" "$SVC"
    ok "建好，shell=$nologin"
fi

# ---------------------------------------------------------------------------
# 2. 存储根
# ---------------------------------------------------------------------------
printf '2/4 存储根 %s\n' "$STORE"
install -d -o "$SVC" -g "$(id -gn "$SVC")" -m 0700 "$STORE"
chown "$SVC:$(id -gn "$SVC")" "$STORE"
chmod 0700 "$STORE"
ok "0700 $SVC"

# ---------------------------------------------------------------------------
# 3. 二进制
#
# 这是整套设计里最关键的一个权限：二进制若能被 agent 改写，agent 就能让自己的代码
# 以 wbsvr 身份跑，托管等于不存在。所以 root 属主 + 0755，装完立刻回验。
# ---------------------------------------------------------------------------
printf '3/4 二进制 %s\n' "$BIN"
install -d -o root -g "$ROOT_GROUP" -m 0755 "$(dirname "$BIN")"
install -o root -g "$ROOT_GROUP" -m 0755 "$STAGE" "$BIN"
rm -f "$STAGE"
ok "$(cd "$(dirname "$BIN")" && ls -l "$(basename "$BIN")" | awk '{print $1, $3, $4}')"
say "sha256 $(shasum -a 256 "$BIN" 2>/dev/null || sha256sum "$BIN" | cut -d' ' -f1)"

# ---------------------------------------------------------------------------
# 4. sudoers
# ---------------------------------------------------------------------------
printf '4/4 允许清单 %s\n' "$SUDOERS"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
sed "s/__AGENT_USER__/$AGENT_USER/g" "$SRC/wbsvr.sudoers" > "$tmp"
# 语法错的 sudoers 会锁死整台机器的 sudo，校验不通过绝不落盘。
visudo -cf "$tmp" >/dev/null || die "生成的 sudoers 语法不对，没有落盘。原始模板：$SRC/wbsvr.sudoers"
install -o root -g "$ROOT_GROUP" -m 0440 "$tmp" "$SUDOERS"
ok "已装并通过 visudo -c"
grep -qE '^#includedir[[:space:]]+/(private/)?etc/sudoers\.d' /etc/sudoers \
    || warn "/etc/sudoers 里没有 #includedir .../sudoers.d —— 这份清单不会生效，手工加进去"

# ---------------------------------------------------------------------------
# 自检。以 agent 身份跑，因为要验的正是「agent 能做什么、不能做什么」。
# ---------------------------------------------------------------------------
FAILED=0
printf '\n自检（以 %s 身份）\n' "$AGENT_USER"
as_agent() { sudo -u "$AGENT_USER" -H "$@"; }

as_agent sudo -n -u "$SVC" "$BIN" ping >/dev/null 2>&1 \
    && ok "agent 能跑 wbsvrd ping" \
    || bad "agent 跑不了 wbsvrd ping —— 清单没生效，检查 $SUDOERS"

# 这条是核心检查：拿得到 shell 则整套隔离归零，其余检查全部无意义。
if as_agent sudo -n -u "$SVC" /bin/sh -c true >/dev/null 2>&1; then
    bad "agent 能以 $SVC 身份拿到 shell —— 整套隔离归零，先修这个"
else
    ok "agent 拿不到 $SVC 的 shell"
fi

for forbidden in unlock sealed-set init; do
    if as_agent sudo -n -u "$SVC" "$BIN" "$forbidden" x y >/dev/null 2>&1; then
        bad "agent 能跑 $forbidden —— 它不该在清单里"
    else
        ok "agent 跑不了 $forbidden"
    fi
done

as_agent test -w "$BIN" && bad "agent 能写 wbsvrd 本体 —— 它能让自己的代码以 $SVC 跑" \
                        || ok "agent 写不了 wbsvrd 本体"
as_agent test -r "$STORE" && bad "agent 能读存储根 —— 应该是 0700 $SVC" \
                          || ok "agent 寻址不到存储根"

# 开发机上这个洞通常真实存在：sudo 密码缓存缓存的是认证不是授权，但 agent 账户一旦
# 命中 %admin ALL=(ALL) ALL，用户刚输过密码的 15 分钟内它就能直接切过去。
groups_of=$(id -Gn "$AGENT_USER")
for g in admin wheel sudo; do
    case " $groups_of " in
        *" $g "*) warn "agent 在 $g 组里 —— 用户刚 sudo 过的窗口内它能绕过整套隔离。要真隔离得把 agent 挪出这个组" ;;
    esac
done

printf '\n'
if [ "$FAILED" -gt 0 ]; then
    printf '%s 项自检没过。前提不成立时工作台不会启用托管模式，会退回 hook + 哈希。\n' "$FAILED"
    exit 1
fi
printf '装好了。下一步在项目里：\n'
printf '  sudo -u %s %s init "$PWD"  < 初始 sealed（由 wb.py init 生成）\n' "$SVC" "$BIN"
printf '  python3 .claude/hooks/wb.py doctor\n'

# 卸载：
#   sudo rm -f /etc/sudoers.d/wbsvr /usr/local/libexec/wbsvrd
#   sudo rm -rf /var/lib/wbsvr                        # 连所有冻结状态一起没了
#   macOS: sudo dscl . -delete /Users/wbsvr && sudo dscl . -delete /Groups/wbsvr
#   Linux: sudo userdel wbsvr
