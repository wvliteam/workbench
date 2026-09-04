// wbsvrd —— 工作台契约托管服务。
//
// 设计与全部决策理由见 docs/wbsvr.md。这个二进制是 sudo 目标：以专用账户 wbsvr
// 身份运行，独占 /var/lib/wbsvr/<项目> 下的存储，而调用它的 agent 账户对那个目录
// 连 ls 都不行。「不可寻址」而不是「不可写」—— 符号链接、tmp+mv、git checkout、
// 权限位配错这一整类攻击一次性消失，不是被逐条防住。
//
// 四条实现约束，改动时不要动摇：
//
//  1. 参数只收 name / 项目路径这类标识符，存储路径一律由本进程拼。sudoers 的 `*`
//     在参数位跨 `/` 且不规范化 `..` —— `wbsvrd read *` 会放行 `read ../../etc/shadow`。
//     防穿越唯一必要也唯一充分的检查在 refName / storeDir 里。
//
//  2. 正文只经 stdin/stdout 传递，**永不写 agent 侧路径**。写 agent 侧要么给它开
//     others 写位、要么搞组共享，两者都属于「配错就静默失效」的权限（D7）。
//     所以冻结清单快照由 wb.py 落盘，不由本进程落 —— 这是对 D15 描述的一处偏离，
//     理由是非 root 进程无法在 agent 拥有的目录里建文件而不放宽那个目录的权限。
//
//  3. 纯 stdlib、静态二进制、零依赖。特权组件的依赖树越小越好，且单文件 sha256
//     可固定，被替换即可检测。
//
//  4. 允许清单优于黑名单。子命令是有限的枚举集合，未知子命令一律拒。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const version = "1.0.0"

// 解冻窗口默认时长。窗口过期由本进程在每次加载 refs 时判定 —— agent 侧的
// SubagentStop hook 不触发就永久开着，冻结**静默失效**，而静默降级是最坏的
// 失败模式（D13）。
const defaultUnlockTTL = 1800

// 存储根。刻意不做 flag / 环境变量：那两条都能让 agent 把服务指向别处，
// 而 sudo 的 env_reset 只在 sudoers 配对时才清得干净。测试里直接改这个变量。
var storeBase = "/var/lib/wbsvr"

// 契约名同时是存储里的文件名，所以它是信任边界上的输入。
var nameRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)

// Ref 是一份托管对象的权威元数据。
//
// 刻意**不含 path / owner / consumers**：那些是「这份契约是什么」，归 agent 侧的
// state.json，改了不影响安全。本结构只管「能不能改」与内容指纹，那两样才是权威。
type Ref struct {
	// Sha 是**锁定那一刻固定下来的期望指纹**，不是「当前内容的指纹」。commit 刻意
	// 不更新它：更新了就等于每次改动都把期望值跟着挪一遍，verify 永远一致，
	// 版本号也永远不涨 —— 冻结就只剩个名字。
	Sha         string `json:"sha"`
	Locked      bool   `json:"locked"`
	UnlockUntil int64  `json:"unlock_until,omitempty"`
	Version     int    `json:"version"`
	// hosted：正文在本存储里，agent 地址空间里根本不存在。
	// repo：正文留在 git 仓库给团队用，本存储只留 sha —— 只有哈希这一档保护。
	Kind     string `json:"kind"`
	Reason   string `json:"reason,omitempty"`
	LockedAt string `json:"locked_at,omitempty"`
	// DisputeReason 非空时，该契约处于争议状态。developer 角色全线停工（执行记录除外）。
	// 与 UnlockUntil 不同，争议没有 TTL —— 必须显式 dispute --clear 或 bump 解除。
	DisputeReason string `json:"dispute_reason,omitempty"`
}

// Store 是一个项目的托管存储。所有写操作在 flock 内完成：并行 develop 下多个
// subagent 各自 fork 一个 wbsvrd，无锁的读-改-写会丢 refs 更新。
type Store struct {
	dir  string
	lock *os.File
}

func main() {
	err := dispatch(os.Args[1:])
	if errors.Is(err, errExit1) {
		os.Exit(1) // 校验类命令：JSON 结论已经写到 stdout，别再往 stderr 重复一遍
	}
	must(err)
}

// errExit1 是「命令跑完了但结论是不通过」。与真正的错误分开，因为调用方 wb.py
// 要在退出码 1 时仍然解析 stdout 的 JSON。
var errExit1 = errors.New("check failed")

// dispatch 是唯一的子命令分派点。测试走它而不是重造一遍 switch —— 两份分派必然漂移。
func dispatch(args []string) error {
	if len(args) == 0 {
		return errors.New("用法：wbsvrd <子命令> [项目路径] [参数...]；子命令见 docs/wbsvr.md")
	}
	cmd, rest := args[0], args[1:]

	// ping / version 不需要项目：存活自检要能在存储还没建时跑。
	switch cmd {
	case "ping":
		return cmdPing()
	case "version":
		out(map[string]any{"version": version})
		return nil
	}

	if len(rest) == 0 {
		return fmt.Errorf("子命令 %s 需要项目路径", cmd)
	}
	proj, rest := rest[0], rest[1:]

	var err error
	switch cmd {
	case "init":
		err = cmdInit(proj)
	case "list":
		err = cmdList(proj)
	case "read":
		err = cmdRead(proj, rest)
	case "commit":
		err = cmdCommit(proj, rest)
	case "lock":
		err = cmdLock(proj, rest)
	case "unlock":
		err = cmdUnlock(proj, rest)
	case "dispute":
		err = cmdDispute(proj, rest)
	case "dispute-clear":
		err = cmdDisputeClear(proj, rest)
	case "verify":
		err = cmdVerify(proj, rest)
	case "sealed-get":
		err = cmdSealedGet(proj, rest)
	case "sealed-set":
		err = cmdSealedSet(proj, rest)
	case "phase-advance":
		err = cmdPhaseAdvance(proj, rest)
	case "tasks-graph-set":
		err = cmdTasksGraphSet(proj)
	case "selfcheck":
		err = cmdSelfcheck(proj)
	default:
		// 允许清单：未知子命令一律拒，不做前缀匹配、不做猜测。
		err = fmt.Errorf("未知子命令：%s", cmd)
	}
	return err
}

// --------------------------------------------------------------------------
// 存储层
// --------------------------------------------------------------------------

// storeDir 把项目路径映射成存储目录名。路径参与 hash 是为了让同名项目在不同
// 路径下互不干扰 —— 否则两个都叫 workbench 的仓库会共用一份冻结状态。
func storeDir(proj string) (string, error) {
	if !filepath.IsAbs(proj) {
		return "", fmt.Errorf("项目路径必须是绝对路径：%s", proj)
	}
	clean := filepath.Clean(proj)
	sum := sha256.Sum256([]byte(clean))
	base := filepath.Base(clean)
	// basename 只用于人眼可读，仍要过滤：项目目录名可以含 `/` 之外的任意字符。
	safe := regexp.MustCompile(`[^A-Za-z0-9._-]`).ReplaceAllString(base, "_")
	if safe == "" || strings.HasPrefix(safe, ".") {
		safe = "proj"
	}
	return filepath.Join(storeBase, fmt.Sprintf("%s-%s", safe, hex.EncodeToString(sum[:])[:8])), nil
}

// openStore 打开已存在的存储并取排他锁。写与读都走它 —— 读也持锁的成本是一次
// flock，换来的是「读到的 refs 与 docs 属于同一个瞬间」。
func openStore(proj string) (*Store, error) {
	dir, err := storeDir(proj)
	if err != nil {
		return nil, err
	}
	st, err := os.Stat(dir)
	if err != nil {
		return nil, fmt.Errorf("该项目未启用托管（存储不存在）：%s。先由用户运行 wbsvrd init", proj)
	}
	if !st.IsDir() {
		return nil, fmt.Errorf("存储路径不是目录：%s", dir)
	}
	fh, err := os.OpenFile(filepath.Join(dir, ".lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, fmt.Errorf("打不开存储锁：%w", err)
	}
	if err := syscall.Flock(int(fh.Fd()), syscall.LOCK_EX); err != nil {
		fh.Close()
		return nil, fmt.Errorf("等存储锁失败：%w", err)
	}
	return &Store{dir: dir, lock: fh}, nil
}

func (s *Store) close() {
	if s.lock != nil {
		syscall.Flock(int(s.lock.Fd()), syscall.LOCK_UN)
		s.lock.Close()
		s.lock = nil
	}
}

func (s *Store) refsPath() string   { return filepath.Join(s.dir, "refs.json") }
func (s *Store) sealedPath() string { return filepath.Join(s.dir, "sealed.json") }
func (s *Store) auditPath() string  { return filepath.Join(s.dir, "audit.jsonl") }

// docPath 是防路径穿越的唯一必要检查点：agent 传进来的只有 name，路径由这里拼。
func (s *Store) docPath(name string) (string, error) {
	if err := refName(name); err != nil {
		return "", err
	}
	return filepath.Join(s.dir, "docs", name), nil
}

func refName(name string) error {
	if !nameRe.MatchString(name) || strings.Contains(name, "..") {
		return fmt.Errorf("非法契约名：%q（只允许字母数字与 . _ -，首字符是字母或数字）", name)
	}
	return nil
}

// loadRefs 顺带过期解冻窗口。过期判定必须在这里而不是调用点：漏一个调用点就是
// 一个「窗口永久开着」的洞，而那个洞是静默的。
func (s *Store) loadRefs() (map[string]*Ref, error) {
	refs := map[string]*Ref{}
	b, err := os.ReadFile(s.refsPath())
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return refs, nil
		}
		return nil, err
	}
	if err := json.Unmarshal(b, &refs); err != nil {
		return nil, fmt.Errorf("refs.json 损坏：%w", err)
	}
	if expireUnlocks(refs, time.Now().Unix()) {
		if err := s.saveRefs(refs); err != nil {
			return nil, err
		}
	}
	return refs, nil
}

// expireUnlocks 把超时的解冻窗口收回。返回是否有变更。
func expireUnlocks(refs map[string]*Ref, now int64) bool {
	changed := false
	for _, r := range refs {
		if r.UnlockUntil != 0 && now >= r.UnlockUntil {
			r.Locked, r.UnlockUntil, r.Reason = true, 0, ""
			changed = true
		}
	}
	return changed
}

// writable 判断这份 ref 现在能不能被 commit 覆盖。
func (r *Ref) writable(now int64) bool {
	if !r.Locked {
		return true
	}
	return r.UnlockUntil != 0 && now < r.UnlockUntil
}

func (s *Store) saveRefs(refs map[string]*Ref) error {
	return writeAtomic(s.refsPath(), mustJSON(refs), 0600)
}

func (s *Store) loadSealed() (map[string]any, error) {
	sealed := map[string]any{}
	b, err := os.ReadFile(s.sealedPath())
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return sealed, nil
		}
		return nil, err
	}
	if err := json.Unmarshal(b, &sealed); err != nil {
		return nil, fmt.Errorf("sealed.json 损坏：%w", err)
	}
	return sealed, nil
}

func (s *Store) saveSealed(sealed map[string]any) error {
	return writeAtomic(s.sealedPath(), mustJSON(sealed), 0600)
}

// audit 是 append-only 的。它值得做，因为「契约不被非预期修改」这个目标里，
// 日志本身可写就等于检测不到 —— 现在的流程日志在 agent 可写的状态文件里（D14）。
// 只记本进程处理的操作：agent 侧发生的事这里记不全，半个日志比没有更坏。
func (s *Store) audit(op, name string, fields map[string]any) {
	e := map[string]any{
		"at":   time.Now().Format(time.RFC3339),
		"op":   op,
		"by":   caller(),
		"pid":  os.Getpid(),
		"name": name,
	}
	for k, v := range fields {
		e[k] = v
	}
	fh, err := os.OpenFile(s.auditPath(), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return // 审计写失败不该让操作失败；selfcheck 会把不可写的审计报出来
	}
	defer fh.Close()
	// 紧凑 JSON：一行一条是 append-only 日志能被 tail / grep 的前提，缩进会毁掉它。
	line, err := json.Marshal(e)
	if err != nil {
		return
	}
	fh.Write(append(line, '\n'))
}

// caller 是原始调用者，不是 wbsvr 自己。sudo 总是设 SUDO_USER/SUDO_UID，
// 且它们在 env_reset 下保留 —— 审计要记「谁让我干的」。
func caller() string {
	if u := os.Getenv("SUDO_USER"); u != "" {
		return u + "(" + os.Getenv("SUDO_UID") + ")"
	}
	if u, err := user.Current(); err == nil {
		return u.Username + "(direct)"
	}
	return "unknown"
}

func writeAtomic(path string, data []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	// 临时文件必须与目标同目录：跨文件系统 rename 不是原子的。名字带 pid，
	// 并行调用共用一个名字时会把彼此的字节交织进去。
	tmp := filepath.Join(dir, fmt.Sprintf(".%s.%d.tmp", filepath.Base(path), os.Getpid()))
	if err := os.WriteFile(tmp, data, mode); err != nil {
		return err
	}
	if err := os.Chmod(tmp, mode); err != nil { // WriteFile 受 umask 影响，明确设一次
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

func shaBytes(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// --------------------------------------------------------------------------
// 子命令
// --------------------------------------------------------------------------

func cmdPing() error {
	u, _ := user.Current()
	name := "?"
	if u != nil {
		name = u.Username
	}
	res := map[string]any{
		"ok": true, "version": version, "user": name,
		"uid": os.Getuid(), "store_base": storeBase, "caller": caller(),
	}
	// 以 root 跑等于把整套隔离降级成「root 帮 agent 干活」，必须说出来。
	if os.Getuid() == 0 {
		res["warn"] = "wbsvrd 正以 root 运行 —— 它应该以专用账户 wbsvr 运行"
	}
	if st, err := os.Stat(storeBase); err != nil {
		res["store_base_ok"] = false
		res["store_base_error"] = err.Error()
	} else {
		res["store_base_ok"] = st.IsDir()
	}
	out(res)
	return nil
}

// cmdInit 建存储。**见到已存在的存储必须硬拒** —— 否则 init 就是「一键清空所有
// 冻结」，同一仓库重开需求只要跑一次 init 就把全部锁定状态归零。重开走用户凭证：
// 用户自己删存储目录。
func cmdInit(proj string) error {
	dir, err := storeDir(proj)
	if err != nil {
		return err
	}
	if _, err := os.Stat(dir); err == nil {
		return fmt.Errorf("存储已存在：%s。init 会清空全部冻结状态，所以见到已存在必须拒绝。"+
			"确实要重开需求：先归档旧状态，再由用户手工 rm -rf 该目录", dir)
	}
	if err := os.MkdirAll(filepath.Join(dir, "docs"), 0700); err != nil {
		return fmt.Errorf("建存储失败：%w（/var/lib/wbsvr 是否存在且属于 wbsvr？）", err)
	}
	// MkdirAll 受 umask 影响，逐级明确设一次 —— 0700 是「agent 连 ls 都不行」的全部依据。
	for _, d := range []string{dir, filepath.Join(dir, "docs")} {
		if err := os.Chmod(d, 0700); err != nil {
			return err
		}
	}
	// 初始 sealed 从 stdin 读。这样阶段列表由 wb.py 提供，wbsvrd 不硬编码阶段名 ——
	// 两份阶段定义必然漂移。
	sealed := map[string]any{}
	if raw, _ := io.ReadAll(os.Stdin); len(strings.TrimSpace(string(raw))) > 0 {
		if err := json.Unmarshal(raw, &sealed); err != nil {
			return fmt.Errorf("stdin 的初始 sealed 不是合法 JSON：%w", err)
		}
	}
	s := &Store{dir: dir}
	if err := s.saveSealed(sealed); err != nil {
		return err
	}
	if err := s.saveRefs(map[string]*Ref{}); err != nil {
		return err
	}
	s.audit("init", "", map[string]any{"project": filepath.Clean(proj)})
	out(map[string]any{"ok": true, "store": dir, "sealed_keys": keysOf(sealed)})
	return nil
}

func cmdList(proj string) error {
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	out(map[string]any{"store": s.dir, "refs": refs})
	return nil
}

// cmdRead 把正文原样写到 stdout，不包 JSON —— 大文档的 JSON 转义纯属浪费，
// 而调用方 wb.py 对这一个命令走 raw 分支。
func cmdRead(proj string, rest []string) error {
	if len(rest) < 1 {
		return errors.New("read 需要契约名")
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	name := rest[0]
	r, has := refs[name]
	if !has {
		return fmt.Errorf("契约未登记：%s", name)
	}
	if r.Kind != "hosted" {
		return fmt.Errorf("%s 是 %s 类契约，正文不在托管存储里，直接读仓库文件", name, r.Kind)
	}
	p, err := s.docPath(name)
	if err != nil {
		return err
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return fmt.Errorf("正文缺失：%s（存储损坏，跑 wbsvrd selfcheck）", name)
	}
	os.Stdout.Write(b)
	return nil
}

// cmdCommit 用 stdin 覆盖正文。已锁定且不在解冻窗口内 = 拒绝，这是整套机制的
// 主判定点：它由文件系统权限而不是命令文本正则支撑，agent 绕不过去。
func cmdCommit(proj string, rest []string) error {
	if len(rest) < 1 {
		return errors.New("commit 需要契约名")
	}
	name := rest[0]
	if err := refName(name); err != nil {
		return err
	}
	body, err := io.ReadAll(os.Stdin)
	if err != nil {
		return fmt.Errorf("读 stdin 失败：%w", err)
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	r, has := refs[name]
	if !has {
		// 首次 commit 即登记。单独的 add 命令是多余的一次往返。
		r = &Ref{Kind: "hosted", Version: 1}
		refs[name] = r
	}
	if r.Kind != "hosted" {
		return fmt.Errorf("%s 是 %s 类契约，正文归 git 管，不能 commit 进托管存储", name, r.Kind)
	}
	if !r.writable(now) {
		return fmt.Errorf("%s 已锁定（v%d），不能改。要改先申报：\n"+
			"  sudo -u wbsvr wbsvrd unlock %s %s '<为什么要改>'\n"+
			"申报理由必须在改之前写 —— 事后补的理由都是给已发生的事找解释",
			name, r.Version, filepath.Clean(proj), name)
	}
	p, err := s.docPath(name)
	if err != nil {
		return err
	}
	// 旧正文的指纹从磁盘算，不从 r.Sha 取 —— r.Sha 是期望值，两者在解冻窗口里不同。
	old := ""
	if prev, err := os.ReadFile(p); err == nil {
		old = shaBytes(prev)
	}
	if err := writeAtomic(p, body, 0600); err != nil {
		return err
	}
	newSha := shaBytes(body)
	// 刻意不写 r.Sha。见 Ref.Sha 的注释。
	if err := s.saveRefs(refs); err != nil {
		return err
	}
	s.audit("commit", name, map[string]any{
		"old_sha": short(old), "new_sha": short(newSha), "bytes": len(body),
		"was_locked": r.Locked, "reason": r.Reason,
	})
	out(map[string]any{"ok": true, "name": name, "sha": newSha, "bytes": len(body),
		"version": r.Version, "locked": r.Locked, "expect": r.Sha})
	return nil
}

// cmdLock 重算并固定内容指纹。
//
// 第三个位置参数给 sha 时是 repo 类契约：正文留在 git 仓库里给团队用，本进程
// 读不到（也不该读 agent 侧路径，见 D7/约束 2），只记 wb.py 算出来的 sha。
// 那一档只有哈希保护 —— 这是真实的能力边界，不是偷懒。
func cmdLock(proj string, rest []string) error {
	if len(rest) < 1 {
		return errors.New("lock 需要契约名")
	}
	name := rest[0]
	external := ""
	if len(rest) > 1 {
		external = rest[1]
		if !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(external) {
			return fmt.Errorf("外部 sha 必须是 64 位小写十六进制：%q", external)
		}
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	r, has := refs[name]
	if !has {
		if external == "" {
			return fmt.Errorf("契约未登记：%s（hosted 契约先 commit 正文，repo 契约 lock 时带上 sha）", name)
		}
		if err := refName(name); err != nil {
			return err
		}
		r = &Ref{Kind: "repo", Version: 1}
		refs[name] = r
	}
	newSha := external
	if r.Kind == "hosted" {
		if external != "" {
			return fmt.Errorf("%s 是 hosted 契约，sha 由服务端从正文重算，不接受外部值", name)
		}
		p, err := s.docPath(name)
		if err != nil {
			return err
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return fmt.Errorf("正文缺失：%s（先 commit 再 lock）", name)
		}
		newSha = shaBytes(b)
	} else if external == "" {
		return fmt.Errorf("%s 是 %s 类契约，lock 必须带上 wb.py 算出的 sha", name, r.Kind)
	}
	old := r.Sha
	// 已锁定的期望值只能在解冻窗口内改。少了这一条，lock 自己就是绕过冻结的路：
	// 改 repo 契约的文件 → 用新内容的 sha 重新 lock → verify 永远一致。hosted 那边
	// commit 已经被拦住，但 repo 类的正文在 agent 侧，只有这一层能要求它先申报。
	if r.Locked && old != "" && old != newSha && !r.writable(now) {
		return fmt.Errorf("%s 已锁定（v%d），期望指纹 %s 不能直接换成 %s。要改先申报：\n"+
			"  sudo -u wbsvr wbsvrd unlock %s %s '<为什么要改>'\n"+
			"申报理由必须在改之前写 —— 事后补的理由都是给已发生的事找解释",
			name, r.Version, short(old), short(newSha), filepath.Clean(proj), name)
	}
	if r.Locked && old != "" && old != newSha {
		r.Version++
	}
	r.Sha, r.Locked, r.UnlockUntil, r.LockedAt = newSha, true, 0, time.Now().Format(time.RFC3339)
	reason := r.Reason
	r.Reason = ""
	r.DisputeReason = "" // lock/bump 自动解除争议
	if err := s.saveRefs(refs); err != nil {
		return err
	}
	s.audit("lock", name, map[string]any{
		"old_sha": short(old), "new_sha": short(newSha),
		"version": r.Version, "kind": r.Kind, "reason": reason,
	})
	out(map[string]any{"ok": true, "name": name, "sha": newSha,
		"version": r.Version, "kind": r.Kind, "bumped": old != "" && old != newSha})
	return nil
}

// cmdUnlock 开解冻窗口。**不在 agent 的 sudoers 里** —— 用户的 sudo 密码就是凭证，
// 它拿不到、不会漏进 transcript、不用管过期（D3）。
func cmdUnlock(proj string, rest []string) error {
	if len(rest) < 2 {
		return errors.New("unlock 需要契约名与理由：wbsvrd unlock <项目> <名> '<为什么要改>'")
	}
	name, reason := rest[0], strings.TrimSpace(rest[1])
	if reason == "" {
		return errors.New("理由不能为空 —— 冻结文档的改动理由要在改之前留痕")
	}
	ttl := int64(defaultUnlockTTL)
	if len(rest) > 2 {
		v, err := strconv.ParseInt(rest[2], 10, 64)
		if err != nil || v <= 0 || v > 86400 {
			return fmt.Errorf("窗口秒数要在 1..86400 之间：%q", rest[2])
		}
		ttl = v
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	r, has := refs[name]
	if !has {
		return fmt.Errorf("契约未登记：%s", name)
	}
	until := time.Now().Unix() + ttl
	r.Locked, r.UnlockUntil, r.Reason = true, until, reason
	if err := s.saveRefs(refs); err != nil {
		return err
	}
	s.audit("unlock", name, map[string]any{
		"reason": reason, "until": until, "ttl": ttl, "version": r.Version,
	})
	out(map[string]any{"ok": true, "name": name, "unlock_until": until,
		"ttl": ttl, "reason": reason, "kind": r.Kind})
	return nil
}

// cmdDispute 落争议哨兵。agent 可直接操作 —— 停工是保护动作，安全方向是错的也无害。
// 争议没有 TTL —— 必须显式 dispute-clear 或 bump 解除，不能靠过期自动恢复。
func cmdDispute(proj string, rest []string) error {
	name := ""
	reason := ""
	for i := 0; i < len(rest); i++ {
		switch rest[i] {
		case "--name":
			if i+1 < len(rest) {
				i++
				name = rest[i]
			}
		case "--reason":
			if i+1 < len(rest) {
				i++
				reason = rest[i]
			}
		}
	}
	if name == "" {
		return errors.New("dispute 需要 --name")
	}
	if reason == "" {
		return errors.New("dispute 必须给 --reason —— 冲突在哪要说清楚")
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	r, has := refs[name]
	if !has {
		return fmt.Errorf("契约未登记：%s", name)
	}
	r.DisputeReason = reason
	if err := s.saveRefs(refs); err != nil {
		return err
	}
	s.audit("dispute", name, map[string]any{"reason": reason, "version": r.Version})
	out(map[string]any{"ok": true, "name": name, "dispute_reason": reason})
	return nil
}

// cmdDisputeClear 解除争议。不在 sudoers 里 —— 与 unlock 同理：
// agent 能自己清掉争议的话，争议就只是个建议。用户必须直接跑 wbsvrd dispute-clear。
// bump 走 lock，lock 自动清 DisputeReason，那是「契约已修订」的正式路径，不经过这里。
func cmdDisputeClear(proj string, rest []string) error {
	name := ""
	for i := 0; i < len(rest); i++ {
		if rest[i] == "--name" && i+1 < len(rest) {
			i++
			name = rest[i]
		}
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	if name == "" {
		// 全部解除
		for _, r := range refs {
			r.DisputeReason = ""
		}
	} else {
		r, has := refs[name]
		if !has {
			return fmt.Errorf("契约未登记：%s", name)
		}
		r.DisputeReason = ""
	}
	if err := s.saveRefs(refs); err != nil {
		return err
	}
	s.audit("dispute_clear", name, map[string]any{"all": name == ""})
	out(map[string]any{"ok": true, "cleared": true, "name": name})
	return nil
}

// cmdVerify 校验内容指纹。hosted 的在这里重算比对；repo 的正文在 agent 侧，
// 只把期望 sha 交出去由 wb.py 比 —— 本进程不读 agent 侧路径。
func cmdVerify(proj string, rest []string) error {
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		return err
	}
	only := ""
	if len(rest) > 0 {
		only = rest[0]
		if _, has := refs[only]; !has {
			return fmt.Errorf("契约未登记：%s", only)
		}
	}
	bad, expect, open := []string{}, map[string]string{}, []string{}
	checked := 0
	now := time.Now().Unix()
	for name, r := range refs {
		if only != "" && name != only {
			continue
		}
		checked++
		// 解冻窗口开着 = 正文正在被合法改动，期望值还没重新固定。报成漂移是误报，
		// 但也不能不说 —— 门禁时有窗口开着本身就该被看见。
		if r.Locked && r.writable(now) {
			open = append(open, name)
			continue
		}
		if r.Kind != "hosted" {
			expect[name] = r.Sha
			continue
		}
		if r.Sha == "" {
			continue // 尚未锁定，无期望值
		}
		p, err := s.docPath(name)
		if err != nil {
			bad = append(bad, name+": "+err.Error())
			continue
		}
		b, err := os.ReadFile(p)
		if err != nil {
			bad = append(bad, name+": 正文缺失（存储损坏）")
			continue
		}
		if got := shaBytes(b); got != r.Sha {
			bad = append(bad, fmt.Sprintf("%s: 托管正文漂移 %s -> %s", name, short(r.Sha), short(got)))
		}
	}
	sort.Strings(bad)
	sort.Strings(open)
	out(map[string]any{"ok": len(bad) == 0, "checked": checked, "bad": bad,
		"expect": expect, "unlocked": open})
	if len(bad) > 0 {
		return errExit1
	}
	return nil
}

func cmdSealedGet(proj string, rest []string) error {
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	sealed, err := s.loadSealed()
	if err != nil {
		return err
	}
	if len(rest) == 0 {
		out(sealed)
		return nil
	}
	v, has := sealed[rest[0]]
	if !has {
		return fmt.Errorf("sealed 里没有这个字段：%s（有的是 %s）", rest[0], strings.Join(keysOf(sealed), ", "))
	}
	out(map[string]any{rest[0]: v})
	return nil
}

// cmdSealedSet 写托管字段。**不在 agent 的 sudoers 里** —— role_scopes 与
// gate_commands 是「改它等于自己给自己发权限 / 让门禁永远 PASS」的两个字段。
func cmdSealedSet(proj string, rest []string) error {
	if len(rest) < 2 {
		return errors.New("sealed-set 需要 <key> <value>")
	}
	key, raw := rest[0], rest[1]
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	sealed, err := s.loadSealed()
	if err != nil {
		return err
	}
	var val any
	if err := json.Unmarshal([]byte(raw), &val); err != nil {
		val = raw // 不是 JSON 就当字符串，跟 wb.py config set 的行为一致
	}
	old := sealed[key]
	sealed[key] = val
	if err := s.saveSealed(sealed); err != nil {
		return err
	}
	s.audit("sealed-set", key, map[string]any{"old": old, "new": val})
	out(map[string]any{"ok": true, "key": key, "value": val})
	return nil
}

// cmdPhaseAdvance 是 agent 唯一能改的 sealed 字段，且只能前进一格。
//
// 阶段推进本来就是 agent 的合法操作（门禁通过后推进），但「任意设置 phase」不是 ——
// 那能退回 clarify 再前进，把已过门禁的记录洗掉。约束成「from 必须等于当前值、
// to 必须是紧邻的下一个」之后，agent 能推进但不能跳、不能退。
func cmdPhaseAdvance(proj string, rest []string) error {
	if len(rest) < 2 {
		return errors.New("phase-advance 需要 <from> <to>")
	}
	from, to := rest[0], rest[1]
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	sealed, err := s.loadSealed()
	if err != nil {
		return err
	}
	cur, _ := sealed["phase"].(string)
	if cur != from {
		return fmt.Errorf("阶段已被改到 %s，这次推进的前提（%s）作废，重跑 phase advance", cur, from)
	}
	phases := stringsOf(sealed["phases"])
	idx := indexOf(phases, from)
	if idx < 0 {
		return fmt.Errorf("%s 不在阶段列表里：%s", from, strings.Join(phases, ", "))
	}
	if idx+1 >= len(phases) || phases[idx+1] != to {
		return fmt.Errorf("只能推进到紧邻的下一个阶段。当前 %s，下一个是 %s，你给的是 %s",
			from, nextOr(phases, idx, "（无，已是最后阶段）"), to)
	}
	sealed["phase"] = to
	if err := s.saveSealed(sealed); err != nil {
		return err
	}
	s.audit("phase-advance", to, map[string]any{"from": from, "to": to})
	out(map[string]any{"ok": true, "from": from, "to": to})
	return nil
}

// cmdTasksGraphSet 写任务图的**结构**部分（id / title / role / phase / deps）。
//
// 拆开结构与状态的理由（D9）：`task done` 本来就是 agent 的合法调用，托管它零收益；
// 但 deps 与 role 定义了执行顺序，改 deps 就绕过顺序，而顺序保证是本项目核心目标。
//
// design 阶段之前自由改（还在设计），进入 develop 之后只允许**追加** —— 已有节点的
// role / phase / deps 不能变。`contract bump` 要给消费方建返工任务，所以不能整体封死。
func cmdTasksGraphSet(proj string) error {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		return fmt.Errorf("读 stdin 失败：%w", err)
	}
	var incoming []map[string]any
	if err := json.Unmarshal(raw, &incoming); err != nil {
		return fmt.Errorf("stdin 不是任务数组 JSON：%w", err)
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	sealed, err := s.loadSealed()
	if err != nil {
		return err
	}
	phases := stringsOf(sealed["phases"])
	cur, _ := sealed["phase"].(string)
	frozen := indexOf(phases, cur) > indexOf(phases, "design")

	if frozen {
		oldNodes := nodeIndex(sealed["tasks_graph"])
		newNodes := nodeIndex(incoming)
		for id, o := range oldNodes {
			n, has := newNodes[id]
			if !has {
				return fmt.Errorf("任务 %s 被删掉了。design 定稿之后任务图只能追加 —— "+
					"顺序由结构决定，删节点等于改顺序。确需改：由用户跑 "+
					"`sudo -u wbsvr wbsvrd sealed-set <项目> tasks_graph '<JSON>'`", id)
			}
			for _, f := range []string{"role", "phase", "deps"} {
				if !sameJSON(o[f], n[f]) {
					return fmt.Errorf("任务 %s 的 %s 变了（%s -> %s）。design 定稿之后"+
						"只能追加新任务，不能改已有任务的执行顺序。确需改：由用户跑 "+
						"`sudo -u wbsvr wbsvrd sealed-set <项目> tasks_graph '<JSON>'`",
						id, f, jsonStr(o[f]), jsonStr(n[f]))
				}
			}
		}
	}
	before := len(nodeIndex(sealed["tasks_graph"]))
	sealed["tasks_graph"] = incoming
	if err := s.saveSealed(sealed); err != nil {
		return err
	}
	s.audit("tasks-graph-set", "", map[string]any{
		"before": before, "after": len(incoming), "append_only": frozen,
	})
	out(map[string]any{"ok": true, "tasks": len(incoming), "append_only": frozen})
	return nil
}

// cmdSelfcheck 查存储自身的完整性。
//
// 它检查的是「这套隔离现在真的成立吗」，所以每一条都对应一个会让隔离归零的配错：
// 属主不对 = agent 能直接读写存储；mode 放宽 = 同上；refs 损坏 = 冻结状态未知。
func cmdSelfcheck(proj string) error {
	dir, err := storeDir(proj)
	if err != nil {
		return err
	}
	problems := []string{}
	add := func(f string, a ...any) { problems = append(problems, fmt.Sprintf(f, a...)) }

	fi, err := os.Stat(dir)
	if err != nil {
		return fmt.Errorf("存储不存在：%s", dir)
	}
	if m := fi.Mode().Perm(); m != 0700 {
		add("存储目录权限是 %04o，必须是 0700 —— 放宽一位 agent 就能寻址它", m)
	}
	if sys, ok := fi.Sys().(*syscall.Stat_t); ok && int(sys.Uid) != os.Getuid() {
		add("存储目录属主 uid=%d，不是当前进程的 %d", sys.Uid, os.Getuid())
	}
	s, err := openStore(proj)
	if err != nil {
		return err
	}
	defer s.close()
	refs, err := s.loadRefs()
	if err != nil {
		add("refs.json 不可用：%v", err)
	}
	if _, err := s.loadSealed(); err != nil {
		add("sealed.json 不可用：%v", err)
	}
	hosted, repo := 0, 0
	for name, r := range refs {
		if err := refName(name); err != nil {
			add("refs 里有非法契约名：%q", name)
			continue
		}
		switch r.Kind {
		case "hosted":
			hosted++
			p, _ := s.docPath(name)
			if _, err := os.Stat(p); err != nil {
				add("%s 是 hosted 但正文缺失", name)
			}
		case "repo", "artifact":
			repo++
		default:
			add("%s 的 kind 未知：%q", name, r.Kind)
		}
	}
	fh, err := os.OpenFile(s.auditPath(), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		add("审计日志不可写：%v", err)
	} else {
		fh.Close()
	}
	out(map[string]any{
		"ok": len(problems) == 0, "store": dir, "refs": len(refs),
		"hosted": hosted, "external": repo, "problems": problems,
	})
	if len(problems) > 0 {
		return errExit1
	}
	return nil
}

// --------------------------------------------------------------------------
// 小工具
// --------------------------------------------------------------------------

func out(v any) {
	b, _ := json.MarshalIndent(v, "", "  ")
	os.Stdout.Write(append(b, '\n'))
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "wbsvrd: "+err.Error())
		os.Exit(1)
	}
}

func mustJSON(v any) []byte {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		// refs / sealed 都是 JSON 来的，序列化不回去说明内存里已经不是那份数据了
		panic("序列化失败：" + err.Error())
	}
	return append(b, '\n')
}

func short(sha string) string {
	if len(sha) > 12 {
		return sha[:12]
	}
	return sha
}

func keysOf(m map[string]any) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

func stringsOf(v any) []string {
	arr, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(arr))
	for _, x := range arr {
		if s, ok := x.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func indexOf(ss []string, want string) int {
	for i, s := range ss {
		if s == want {
			return i
		}
	}
	return -1
}

func nextOr(ss []string, idx int, alt string) string {
	if idx+1 < len(ss) {
		return ss[idx+1]
	}
	return alt
}

// nodeIndex 把任务数组变成 {id: 节点}。无 id 的节点丢掉 —— 它无法被比对，
// 而静默放行一个不可比对的节点等于给「只能追加」开一个后门。
func nodeIndex(v any) map[string]map[string]any {
	out := map[string]map[string]any{}
	var arr []any
	switch t := v.(type) {
	case []any:
		arr = t
	case []map[string]any:
		for _, m := range t {
			if id, ok := m["id"].(string); ok && id != "" {
				out[id] = m
			}
		}
		return out
	default:
		return out
	}
	for _, x := range arr {
		if m, ok := x.(map[string]any); ok {
			if id, ok := m["id"].(string); ok && id != "" {
				out[id] = m
			}
		}
	}
	return out
}

func sameJSON(a, b any) bool { return jsonStr(a) == jsonStr(b) }

func jsonStr(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("%v", v)
	}
	return string(b)
}
