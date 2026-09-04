package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// run 把一次子命令跑完，返回 stdout 与 error。走 dispatch 而不是重造分派 switch。
func run(t *testing.T, stdin string, args ...string) (string, error) {
	t.Helper()
	oldOut, oldIn := os.Stdout, os.Stdin

	ro, wo, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	os.Stdout = wo

	if stdin != "" {
		ri, wi, err := os.Pipe()
		if err != nil {
			t.Fatal(err)
		}
		go func() { wi.WriteString(stdin); wi.Close() }()
		os.Stdin = ri
	} else {
		// 空 stdin 必须是已关闭的 pipe，不能留着上一次的 —— io.ReadAll 会挂住。
		ri, wi, _ := os.Pipe()
		wi.Close()
		os.Stdin = ri
	}

	done := make(chan string)
	go func() {
		var sb strings.Builder
		buf := make([]byte, 4096)
		for {
			n, err := ro.Read(buf)
			sb.Write(buf[:n])
			if err != nil {
				break
			}
		}
		done <- sb.String()
	}()

	cmdErr := dispatch(args)
	wo.Close()
	stdout := <-done
	os.Stdout, os.Stdin = oldOut, oldIn
	return stdout, cmdErr
}

func js(t *testing.T, s string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(s), &m); err != nil {
		t.Fatalf("输出不是 JSON：%v\n%s", err, s)
	}
	return m
}

// newProj 建一个隔离的存储根与项目路径。storeBase 是包级变量，所以测试串行跑。
func newProj(t *testing.T) string {
	t.Helper()
	storeBase = filepath.Join(t.TempDir(), "store")
	if err := os.MkdirAll(storeBase, 0755); err != nil {
		t.Fatal(err)
	}
	proj := filepath.Join(t.TempDir(), "demo")
	if err := os.MkdirAll(proj, 0755); err != nil {
		t.Fatal(err)
	}
	sealed := `{"phase":"clarify","phases":["clarify","analyze","design","develop","verify","retro"],
	            "role_scopes":{"pm":["docs/**"]},"gate_commands":{},"gate_timeout":1800,"tasks_graph":[]}`
	if _, err := run(t, sealed, "init", proj); err != nil {
		t.Fatalf("init 失败：%v", err)
	}
	return proj
}

func TestInitRefusesExistingStore(t *testing.T) {
	proj := newProj(t)
	// 二次 init 必须硬拒：否则 init 就是「一键清空所有冻结」。
	_, err := run(t, "{}", "init", proj)
	if err == nil {
		t.Fatal("重复 init 应被拒绝 —— 它会清空全部冻结状态")
	}
	if !strings.Contains(err.Error(), "存储已存在") {
		t.Fatalf("拒绝理由要说清楚，实际：%v", err)
	}
}

func TestStoreModeIs0700(t *testing.T) {
	proj := newProj(t)
	dir, _ := storeDir(proj)
	for _, d := range []string{dir, filepath.Join(dir, "docs")} {
		fi, err := os.Stat(d)
		if err != nil {
			t.Fatal(err)
		}
		// 放宽一位 agent 就能寻址存储，整套「不可寻址」归零。
		if m := fi.Mode().Perm(); m != 0700 {
			t.Fatalf("%s 权限是 %04o，必须 0700", d, m)
		}
	}
}

func TestHostedLifecycle(t *testing.T) {
	proj := newProj(t)

	// 首次 commit 即登记，未锁定时可以自由改
	out, err := run(t, "openapi: 3.0.0\n", "commit", proj, "user-api")
	if err != nil {
		t.Fatal(err)
	}
	first := js(t, out)["sha"].(string)

	if _, err := run(t, "openapi: 3.0.1\n", "commit", proj, "user-api"); err != nil {
		t.Fatalf("未锁定的契约应可自由改：%v", err)
	}

	// lock 之后 commit 必须被拒 —— 这是整套机制的主判定点
	if _, err := run(t, "", "lock", proj, "user-api"); err != nil {
		t.Fatal(err)
	}
	_, err = run(t, "openapi: 9.9.9\n", "commit", proj, "user-api")
	if err == nil {
		t.Fatal("已锁定的契约不该能 commit")
	}
	if !strings.Contains(err.Error(), "unlock") {
		t.Fatalf("拒绝信息要给出申报命令，实际：%v", err)
	}

	// 正文没被那次失败的 commit 动过
	body, err := run(t, "", "read", proj, "user-api")
	if err != nil {
		t.Fatal(err)
	}
	if body != "openapi: 3.0.1\n" {
		t.Fatalf("被拒的 commit 不该改到正文，实际：%q", body)
	}

	// 申报后可以改，lock 时版本号跟着涨
	if _, err := run(t, "", "unlock", proj, "user-api", "补分页字段"); err != nil {
		t.Fatal(err)
	}
	if _, err := run(t, "openapi: 3.1.0\n", "commit", proj, "user-api"); err != nil {
		t.Fatalf("解冻窗口内应放行：%v", err)
	}
	out, err = run(t, "", "lock", proj, "user-api")
	if err != nil {
		t.Fatal(err)
	}
	m := js(t, out)
	if v := m["version"].(float64); v != 2 {
		t.Fatalf("内容变了应 bump 到 v2，实际 v%v", v)
	}
	if m["sha"].(string) == first {
		t.Fatal("sha 应随正文变化")
	}

	// lock 顺带收回窗口：下一次 commit 又该被拒
	if _, err := run(t, "x\n", "commit", proj, "user-api"); err == nil {
		t.Fatal("lock 之后窗口应已关闭")
	}
}

func TestUnlockExpires(t *testing.T) {
	proj := newProj(t)
	run(t, "a\n", "commit", proj, "spec")
	run(t, "", "lock", proj, "spec")
	// 1 秒窗口，等它过期。过期判定必须在服务端：靠 agent 侧 hook 关窗口，
	// 不触发就永久开着，冻结静默失效（D13）。
	if _, err := run(t, "", "unlock", proj, "spec", "试一下", "1"); err != nil {
		t.Fatal(err)
	}
	time.Sleep(1100 * time.Millisecond)
	if _, err := run(t, "b\n", "commit", proj, "spec"); err == nil {
		t.Fatal("窗口过期后应自动重锁")
	}
	out, _ := run(t, "", "list", proj)
	refs := js(t, out)["refs"].(map[string]any)["spec"].(map[string]any)
	if refs["locked"] != true {
		t.Fatal("过期后 locked 应回到 true")
	}
	if _, has := refs["unlock_until"]; has {
		t.Fatal("过期后 unlock_until 应清掉")
	}
}

func TestPathTraversalRejected(t *testing.T) {
	proj := newProj(t)
	// sudoers 的 `*` 在参数位跨 `/` 且不规范化 `..`，所以这一层校验是唯一防线。
	for _, bad := range []string{
		"../../etc/shadow", "..", "a/b", "/etc/passwd", ".hidden", "", "a b",
	} {
		if _, err := run(t, "x", "commit", proj, bad); err == nil {
			t.Fatalf("非法契约名 %q 应被拒", bad)
		}
		if _, err := run(t, "", "read", proj, bad); err == nil {
			t.Fatalf("非法契约名 %q 的 read 应被拒", bad)
		}
	}
	// 存储目录外没有被创建出任何东西
	if _, err := os.Stat(filepath.Join(storeBase, "..", "etc")); err == nil {
		t.Fatal("穿越写出了文件")
	}
}

func TestRelativeProjectRejected(t *testing.T) {
	newProj(t)
	if _, err := run(t, "", "list", "relative/path"); err == nil {
		t.Fatal("相对项目路径应被拒 —— 它让存储目录取决于调用方的 cwd")
	}
}

func TestRepoKindOnlyTakesExternalSha(t *testing.T) {
	proj := newProj(t)
	sha := strings.Repeat("ab", 32)
	out, err := run(t, "", "lock", proj, "openapi.yaml", sha)
	if err != nil {
		t.Fatal(err)
	}
	if k := js(t, out)["kind"].(string); k != "repo" {
		t.Fatalf("带 sha 的 lock 应登记为 repo 类，实际 %s", k)
	}
	// repo 类正文归 git，不能塞进托管存储 —— 否则会有两份权威副本
	if _, err := run(t, "body", "commit", proj, "openapi.yaml"); err == nil {
		t.Fatal("repo 类契约不该能 commit")
	}
	if _, err := run(t, "", "read", proj, "openapi.yaml"); err == nil {
		t.Fatal("repo 类契约不该能从存储 read")
	}
	// hosted 的 sha 由服务端重算，不接受外部值 —— 否则 agent 可以谎报指纹
	run(t, "x\n", "commit", proj, "hosted-one")
	if _, err := run(t, "", "lock", proj, "hosted-one", sha); err == nil {
		t.Fatal("hosted 契约不该接受外部 sha")
	}
	if _, err := run(t, "", "lock", proj, "never-seen", "notasha"); err == nil {
		t.Fatal("格式非法的 sha 应被拒")
	}
}

func TestVerifyDetectsStoreTampering(t *testing.T) {
	proj := newProj(t)
	run(t, "good\n", "commit", proj, "spec")
	run(t, "", "lock", proj, "spec")
	if _, err := run(t, "", "verify", proj); err != nil {
		t.Fatalf("刚锁定应一致：%v", err)
	}
	// 绕过服务直接改存储（模拟存储被 root 或备份恢复动过）
	dir, _ := storeDir(proj)
	os.WriteFile(filepath.Join(dir, "docs", "spec"), []byte("tampered\n"), 0600)
	out, err := run(t, "", "verify", proj)
	if err == nil {
		t.Fatal("正文被外部改过时 verify 应失败")
	}
	m := js(t, out)
	if m["ok"] != false || len(m["bad"].([]any)) != 1 {
		t.Fatalf("verify 应报出漂移：%s", out)
	}
	// 退出码 1 时 stdout 仍要是可解析的 JSON —— wb.py 靠它拿明细
	if !strings.Contains(out, "托管正文漂移") {
		t.Fatalf("漂移说明缺失：%s", out)
	}
}

func TestVerifyHandsBackRepoShaForCallerToCompare(t *testing.T) {
	proj := newProj(t)
	sha := strings.Repeat("cd", 32)
	run(t, "", "lock", proj, "openapi.yaml", sha)
	out, err := run(t, "", "verify", proj)
	if err != nil {
		t.Fatal(err)
	}
	// repo 类正文在 agent 侧，本进程不读 agent 路径（D7），只交出期望值
	if got := js(t, out)["expect"].(map[string]any)["openapi.yaml"]; got != sha {
		t.Fatalf("repo 类应把期望 sha 交给调用方，实际 %v", got)
	}
}

func TestPhaseAdvanceOnlyOneStepForward(t *testing.T) {
	proj := newProj(t)
	if _, err := run(t, "", "phase-advance", proj, "clarify", "analyze"); err != nil {
		t.Fatal(err)
	}
	// 跳跃：会把中间阶段的门禁整个跳过
	if _, err := run(t, "", "phase-advance", proj, "analyze", "retro"); err == nil {
		t.Fatal("跨阶段跳跃应被拒")
	}
	// 后退：能把已过门禁的记录洗掉再重走
	if _, err := run(t, "", "phase-advance", proj, "analyze", "clarify"); err == nil {
		t.Fatal("阶段后退应被拒")
	}
	// from 不匹配：并发下别人已经推过了，这次结论作废
	if _, err := run(t, "", "phase-advance", proj, "clarify", "analyze"); err == nil {
		t.Fatal("from 与当前阶段不符时应拒")
	}
	out, _ := run(t, "", "sealed-get", proj, "phase")
	if p := js(t, out)["phase"]; p != "analyze" {
		t.Fatalf("阶段应停在 analyze，实际 %v", p)
	}
}

func TestTasksGraphAppendOnlyAfterDesign(t *testing.T) {
	proj := newProj(t)
	g1 := `[{"id":"T1","title":"建表","role":"backend-developer","phase":"develop","deps":[]},
	        {"id":"T2","title":"接页面","role":"frontend-developer","phase":"develop","deps":["T1"]}]`
	if _, err := run(t, g1, "tasks-graph-set", proj); err != nil {
		t.Fatal(err)
	}
	// design 之前还在设计，改 deps 是正常的
	g2 := `[{"id":"T1","title":"建表","role":"backend-developer","phase":"develop","deps":["T2"]},
	        {"id":"T2","title":"接页面","role":"frontend-developer","phase":"develop","deps":[]}]`
	if _, err := run(t, g2, "tasks-graph-set", proj); err != nil {
		t.Fatalf("design 之前应可自由改：%v", err)
	}
	if _, err := run(t, g1, "tasks-graph-set", proj); err != nil {
		t.Fatal(err)
	}

	for _, step := range [][2]string{{"clarify", "analyze"}, {"analyze", "design"}, {"design", "develop"}} {
		if _, err := run(t, "", "phase-advance", proj, step[0], step[1]); err != nil {
			t.Fatal(err)
		}
	}

	// 改 deps = 绕过执行顺序，而顺序保证是本项目核心目标之一（D9）
	if _, err := run(t, g2, "tasks-graph-set", proj); err == nil {
		t.Fatal("develop 之后改已有任务的 deps 应被拒")
	}
	// 改 role = 把任务挪给另一个写入范围的角色
	g3 := `[{"id":"T1","title":"建表","role":"frontend-developer","phase":"develop","deps":[]},
	        {"id":"T2","title":"接页面","role":"frontend-developer","phase":"develop","deps":["T1"]}]`
	if _, err := run(t, g3, "tasks-graph-set", proj); err == nil {
		t.Fatal("develop 之后改已有任务的 role 应被拒")
	}
	// 删节点也是改顺序
	g4 := `[{"id":"T1","title":"建表","role":"backend-developer","phase":"develop","deps":[]}]`
	if _, err := run(t, g4, "tasks-graph-set", proj); err == nil {
		t.Fatal("develop 之后删任务应被拒")
	}
	// 追加要放行：contract bump 会给每个消费方建返工任务
	g5 := g1[:len(g1)-1] + `,{"id":"T3","title":"同步契约变更","role":"backend-developer","phase":"develop","deps":[]}]`
	out, err := run(t, g5, "tasks-graph-set", proj)
	if err != nil {
		t.Fatalf("追加新任务应放行（contract bump 依赖它）：%v", err)
	}
	if n := js(t, out)["tasks"].(float64); n != 3 {
		t.Fatalf("追加后应有 3 个任务，实际 %v", n)
	}
	// 只改标题不改结构：放行
	g6 := strings.Replace(g5, `"title":"建表"`, `"title":"建表（补索引）"`, 1)
	if _, err := run(t, g6, "tasks-graph-set", proj); err != nil {
		t.Fatalf("只改标题不该被拒：%v", err)
	}
}

func TestSealedSetAndGet(t *testing.T) {
	proj := newProj(t)
	if _, err := run(t, "", "sealed-set", proj, "gate_commands", `{"test":"npm test"}`); err != nil {
		t.Fatal(err)
	}
	out, _ := run(t, "", "sealed-get", proj, "gate_commands")
	got := js(t, out)["gate_commands"].(map[string]any)
	if got["test"] != "npm test" {
		t.Fatalf("sealed 往返丢了值：%s", out)
	}
	if _, err := run(t, "", "sealed-get", proj, "nope"); err == nil {
		t.Fatal("不存在的 sealed 字段应报错并列出有哪些")
	}
}

func TestAuditIsAppendOnlyAndNamesCaller(t *testing.T) {
	t.Setenv("SUDO_USER", "work")
	t.Setenv("SUDO_UID", "501")
	proj := newProj(t)

	run(t, "a\n", "commit", proj, "spec")
	run(t, "", "lock", proj, "spec")
	run(t, "", "unlock", proj, "spec", "改个字段")
	run(t, "b\n", "commit", proj, "spec")
	run(t, "", "lock", proj, "spec")

	dir, _ := storeDir(proj)
	b, err := os.ReadFile(filepath.Join(dir, "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(b)), "\n")
	// init + 2 commit + 2 lock + 1 unlock
	if len(lines) != 6 {
		t.Fatalf("审计条数不对（%d）：\n%s", len(lines), b)
	}
	var ops []string
	for _, l := range lines {
		e := js(t, l)
		ops = append(ops, e["op"].(string))
		if e["by"] != "work(501)" {
			t.Fatalf("审计要记原始调用者不是 wbsvr 自己，实际 %v", e["by"])
		}
	}
	if got := strings.Join(ops, ","); got != "init,commit,lock,unlock,commit,lock" {
		t.Fatalf("审计顺序不对：%s", got)
	}
	// 变更理由必须落在审计里 —— 否则「改了什么、为什么」还是只有 agent 可写的记录
	if !strings.Contains(string(b), "改个字段") {
		t.Fatal("解冻理由没进审计日志")
	}
	if !strings.Contains(string(b), `"old_sha"`) {
		t.Fatal("审计要记 old_sha/new_sha，那是免费的版本历史")
	}
}

func TestSelfcheckCatchesWidenedPermissions(t *testing.T) {
	proj := newProj(t)
	run(t, "a\n", "commit", proj, "spec")
	run(t, "", "lock", proj, "spec")
	if _, err := run(t, "", "selfcheck", proj); err != nil {
		t.Fatalf("干净存储应自检通过：%v", err)
	}
	dir, _ := storeDir(proj)
	os.Chmod(dir, 0755)
	out, err := run(t, "", "selfcheck", proj)
	if err == nil {
		t.Fatal("存储权限放宽后自检应失败")
	}
	if !strings.Contains(out, "0700") {
		t.Fatalf("自检要点名权限问题：%s", out)
	}
	os.Chmod(dir, 0700)

	// hosted 正文被删 = 存储损坏，要报出来而不是当成空文档
	os.Remove(filepath.Join(dir, "docs", "spec"))
	if _, err := run(t, "", "selfcheck", proj); err == nil {
		t.Fatal("hosted 正文缺失时自检应失败")
	}
}

func TestUnknownSubcommandRejected(t *testing.T) {
	proj := newProj(t)
	for _, bad := range []string{"rea", "commit-all", "exec", "sh"} {
		if _, err := run(t, "", bad, proj); err == nil {
			t.Fatalf("未知子命令 %q 应被拒（允许清单优于黑名单）", bad)
		}
	}
}

func TestStoreDirIsPathScoped(t *testing.T) {
	storeBase = filepath.Join(t.TempDir(), "store")
	a, _ := storeDir("/home/work/repos/foo")
	b, _ := storeDir("/home/other/repos/foo")
	if a == b {
		t.Fatal("同名项目在不同路径下必须映射到不同存储")
	}
	// 尾斜杠与 .. 规范化后应指向同一份存储
	c, _ := storeDir("/home/work/repos/bar/../foo/")
	if c != a {
		t.Fatalf("路径规范化不一致：%s vs %s", c, a)
	}
	if !strings.HasPrefix(filepath.Base(a), "foo-") {
		t.Fatalf("存储目录名应带可读的项目名：%s", a)
	}
}

func TestStoreLockSerializesWriters(t *testing.T) {
	proj := newProj(t)
	// 并行 develop 下多个 subagent 各自 fork 一个 wbsvrd。无锁的读-改-写会丢 refs
	// 更新：先落盘的那份登记被后一个进程的旧快照盖掉。flock 是按 open file
	// description 生效的，所以同进程里两次 openStore 也会真的互斥。
	s1, err := openStore(proj)
	if err != nil {
		t.Fatal(err)
	}
	got := make(chan time.Time, 1)
	go func() {
		s2, err := openStore(proj)
		if err != nil {
			t.Error(err)
			close(got)
			return
		}
		got <- time.Now()
		s2.close()
	}()
	select {
	case <-got:
		t.Fatal("第二个写者没被锁挡住 —— 并发 commit 会丢 refs 更新")
	case <-time.After(150 * time.Millisecond):
	}
	released := time.Now()
	s1.close()
	select {
	case at, ok := <-got:
		if !ok {
			t.Fatal("第二个写者拿锁失败")
		}
		if at.Before(released) {
			t.Fatal("第二个写者在释放之前就拿到了锁")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("锁释放后第二个写者仍拿不到锁")
	}
}

func TestVerifyIgnoresOpenUnlockWindow(t *testing.T) {
	proj := newProj(t)
	run(t, "a\n", "commit", proj, "spec")
	run(t, "", "lock", proj, "spec")
	run(t, "", "unlock", proj, "spec", "补字段")
	run(t, "b\n", "commit", proj, "spec")

	// 窗口内的改动是合法的，期望值还没重新固定 —— 报成漂移是误报
	out, err := run(t, "", "verify", proj)
	if err != nil {
		t.Fatalf("解冻窗口内不该报漂移：%s", out)
	}
	if u := js(t, out)["unlocked"].([]any); len(u) != 1 || u[0] != "spec" {
		t.Fatalf("有窗口开着必须被点名，否则门禁看不见：%s", out)
	}

	// 重新 lock 固定期望值之后，内容一致应通过
	if _, err := run(t, "", "lock", proj, "spec"); err != nil {
		t.Fatal(err)
	}
	if _, err := run(t, "", "verify", proj); err != nil {
		t.Fatalf("重新锁定后应一致：%v", err)
	}
}

func TestRelockCannotReplaceFingerprintWithoutWindow(t *testing.T) {
	proj := newProj(t)
	a := strings.Repeat("a", 64)
	b := strings.Repeat("b", 64)
	if _, err := run(t, "", "lock", proj, "openapi.yaml", a); err != nil {
		t.Fatal(err)
	}
	// repo 类正文在 agent 侧，只有哈希这一档保护 —— 如果 lock 能直接换期望值，
	// 那一档也没了：改文件、按新内容重新 lock、verify 永远一致。
	_, err := run(t, "", "lock", proj, "openapi.yaml", b)
	if err == nil {
		t.Fatal("未申报就换期望指纹应被拒")
	}
	if !strings.Contains(err.Error(), "unlock") {
		t.Fatalf("拒绝信息要给出申报命令：%v", err)
	}
	// 同一个 sha 重新 lock 是幂等的，不该被拦
	if _, err := run(t, "", "lock", proj, "openapi.yaml", a); err != nil {
		t.Fatalf("同指纹重新 lock 应放行：%v", err)
	}
	// 申报之后才能换，且版本号跟着涨
	if _, err := run(t, "", "unlock", proj, "openapi.yaml", "接口加了游标分页"); err != nil {
		t.Fatal(err)
	}
	out, err := run(t, "", "lock", proj, "openapi.yaml", b)
	if err != nil {
		t.Fatal(err)
	}
	if v := js(t, out)["version"].(float64); v != 2 {
		t.Fatalf("期望值变了应 bump 到 v2，实际 v%v", v)
	}
}
