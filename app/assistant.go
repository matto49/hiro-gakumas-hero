package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"

	"github.com/egoist/mygo"
)

// 模型配置和对话入口（设计稿 §8）：照 magpie 的「服务商 + Key + 模型」，简化成三家。
//
//	deepseek  填 API Key（存系统钥匙串，界面只显示「已保存」），走内置的工具调用循环（Agent）
//	codex     用户已登录的 codex 命令行，当外部 agent 跑：读 hiro 的说明、自己调 hiro 命令
//	claude    用户已登录的 claude 命令行，同上
//
// 命令行模式绕不过待确认：它们开局只能拿到待确认编号，跑完后程序把新出现的待确认做成确认卡。

// Settings 是模型设置（不含 Key）。
type Settings struct {
	Provider string `json:"provider"` // "" / deepseek / codex / claude
	Model    string `json:"model,omitempty"`
	BaseURL  string `json:"base_url,omitempty"`
	HasKey   bool   `json:"has_key"`
}

// DefaultDeepSeek 是 DeepSeek 的缺省地址和模型。
const (
	DefaultDeepSeekURL   = "https://api.deepseek.com"
	DefaultDeepSeekModel = "deepseek-chat"
	deepseekKeyName      = "deepseek-api-key"
)

var errNoKey = errors.New("没有保存 Key")

// KeyStore 存模型的 API Key。
type KeyStore interface {
	Get(name string) (string, error)
	Set(name, value string) error
	Delete(name string) error
}

// systemKeys 用系统钥匙串 / 凭据管理器（mygo.Secrets）。
type systemKeys struct{}

func (systemKeys) Get(name string) (string, error) {
	b, err := mygo.Secrets.Get(name)
	if errors.Is(err, mygo.ErrSecretNotFound) {
		return "", errNoKey
	}
	return string(b), err
}
func (systemKeys) Set(name, value string) error { return mygo.Secrets.Set(name, []byte(value)) }
func (systemKeys) Delete(name string) error      { return mygo.Secrets.Delete(name) }

// Assistant 是界面用的对话入口（绑定给网页）。
type Assistant struct {
	Home string   `json:"-"`
	Keys KeyStore `json:"-"`
	Hiro *Hiro    `json:"-"`
}

func (a *Assistant) settingsPath() string { return filepath.Join(a.Home, "settings.json") }

// Settings 读模型设置。
func (a *Assistant) Settings() Settings {
	var s Settings
	if b, err := os.ReadFile(a.settingsPath()); err == nil {
		_ = json.Unmarshal(b, &s)
	}
	s.HasKey = false
	if s.Provider == "deepseek" {
		if k, err := a.Keys.Get(deepseekKeyName); err == nil && k != "" {
			s.HasKey = true
		}
	}
	return s
}

// Save 保存模型设置；key 非空时存进钥匙串（空的不动已存的）。
func (a *Assistant) Save(s Settings, key string) error {
	if s.Provider != "" && s.Provider != "deepseek" && s.Provider != "codex" && s.Provider != "claude" {
		return fmt.Errorf("不认识的服务商 %q", s.Provider)
	}
	if k := strings.TrimSpace(key); k != "" {
		if err := a.Keys.Set(deepseekKeyName, k); err != nil {
			return fmt.Errorf("Key 存不进系统钥匙串：%w", err)
		}
	}
	s.HasKey = false
	b, _ := json.MarshalIndent(s, "", "  ")
	if err := os.MkdirAll(a.Home, 0o755); err != nil {
		return err
	}
	return os.WriteFile(a.settingsPath(), b, 0o600)
}

func (a *Assistant) provider() (ChatProvider, error) {
	s := a.Settings()
	if s.Provider != "deepseek" {
		return nil, errors.New("还没配模型：到设置里选服务商")
	}
	key, err := a.Keys.Get(deepseekKeyName)
	if err != nil || key == "" {
		return nil, errors.New("还没填 DeepSeek 的 API Key：到设置里填")
	}
	url, model := s.BaseURL, s.Model
	if url == "" {
		url = DefaultDeepSeekURL
	}
	if model == "" {
		model = DefaultDeepSeekModel
	}
	return OpenAICompat{BaseURL: url, Model: model, Key: key, Client: &http.Client{Timeout: 120 * time.Second}}, nil
}

// TestResult 是「测试」按钮的结果。
type TestResult struct {
	OK      bool   `json:"ok"`
	Message string `json:"message"`
}

// Test 试一下当前的模型配置能不能用（不调工具）。
func (a *Assistant) Test(ctx context.Context) TestResult {
	s := a.Settings()
	switch s.Provider {
	case "codex", "claude":
		bin, err := exec.LookPath(s.Provider)
		if err != nil {
			return TestResult{Message: fmt.Sprintf("找不到 %s 命令：先装好并登录", s.Provider)}
		}
		return TestResult{OK: true, Message: "找到了 " + bin}
	}
	p, err := a.provider()
	if err != nil {
		return TestResult{Message: err.Error()}
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	m, err := p.Chat(ctx, []chatMessage{{Role: "user", Content: "只回答两个字：可以"}}, nil)
	if err != nil {
		return TestResult{Message: err.Error()}
	}
	return TestResult{OK: true, Message: "模型回答：" + strings.TrimSpace(m.Content)}
}

// Ask 让小广回答一句话（history 是之前的对话）。
func (a *Assistant) Ask(ctx context.Context, history []Turn, text string) (Reply, error) {
	s := a.Settings()
	switch s.Provider {
	case "codex", "claude":
		bin, err := exec.LookPath(s.Provider)
		if err != nil {
			return Reply{}, fmt.Errorf("找不到 %s 命令：先装好并登录，或到设置里换服务商", s.Provider)
		}
		return CLIAgent{Kind: s.Provider, Bin: bin, Hiro: a.Hiro, Home: a.Home}.Ask(ctx, history, text)
	}
	p, err := a.provider()
	if err != nil {
		return Reply{}, err
	}
	tools, err := LoadTools(ctx, a.Hiro)
	if err != nil {
		return Reply{}, fmt.Errorf("工具层不可用：%w", err)
	}
	return (&Agent{Hiro: a.Hiro, Tools: tools, Provider: p}).Ask(ctx, history, text)
}

// ---------------------------------------------------------------------------- 命令行 agent

// CLIAgent 用用户已登录的 claude / codex 命令行当 agent：它们按说明自己调 hiro 命令。
type CLIAgent struct {
	Kind string // claude / codex
	Bin  string
	Hiro *Hiro
	Home string
}

const cliRules = `

工具：在终端里运行 hiro 命令（例如 hiro info summary、hiro tools）。每条命令打印一行 JSON。
会动游戏的命令（hiro run start …）返回「待确认」（退出码 3）：不要运行 hiro confirm，把 summary 告诉玩家，界面会给玩家确认卡。
不要运行 hiro secret set / hiro secret delete。只回答玩家，不要改任何文件。`

func (c CLIAgent) prompt(history []Turn, text string) string {
	var b strings.Builder
	if len(history) > 0 {
		b.WriteString("之前的对话：\n")
		for _, h := range history {
			who := "玩家"
			if h.Role == "assistant" {
				who = "小广"
			}
			b.WriteString(who + "：" + h.Content + "\n")
		}
		b.WriteString("\n")
	}
	b.WriteString("玩家这次说：" + text)
	return b.String()
}

// shim 在 HIRO_HOME/bin 放一个 hiro 命令，指向程序自己的工具层。
func (c CLIAgent) shim() (string, error) {
	dir := filepath.Join(c.Home, "bin")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	cmd := c.Hiro.command()
	if runtime.GOOS == "windows" {
		var parts []string
		for _, s := range cmd {
			parts = append(parts, `"`+s+`"`)
		}
		return dir, os.WriteFile(filepath.Join(dir, "hiro.cmd"), []byte("@echo off\r\n"+strings.Join(parts, " ")+" %*\r\n"), 0o755)
	}
	var parts []string
	for _, s := range cmd {
		parts = append(parts, "'"+strings.ReplaceAll(s, "'", `'\''`)+"'")
	}
	return dir, os.WriteFile(filepath.Join(dir, "hiro"), []byte("#!/bin/sh\nexec "+strings.Join(parts, " ")+` "$@"`+"\n"), 0o755)
}

func (c CLIAgent) pendingIDs(ctx context.Context) map[string]Pending {
	out := map[string]Pending{}
	r := c.Hiro.Call(ctx, []string{"pending", "list"})
	var d struct {
		Pending []Pending `json:"pending"`
	}
	if r.OK && json.Unmarshal(r.Data, &d) == nil {
		for _, p := range d.Pending {
			out[p.ID] = p
		}
	}
	return out
}

func (c CLIAgent) Ask(ctx context.Context, history []Turn, text string) (Reply, error) {
	bin, err := c.shim()
	if err != nil {
		return Reply{}, err
	}
	before := c.pendingIDs(ctx)
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	prompt := c.prompt(history, text)
	var cmd *exec.Cmd
	var lastFile string
	switch c.Kind {
	case "codex":
		lastFile = filepath.Join(c.Home, "codex-last.txt")
		_ = os.Remove(lastFile)
		cmd = exec.CommandContext(ctx, c.Bin, "exec", "--skip-git-repo-check", "-s", "workspace-write",
			"-c", "sandbox_workspace_write.network_access=true", "-C", c.Home, "--output-last-message", lastFile,
			SystemPrompt+cliRules+"\n\n"+prompt)
	default:
		cmd = exec.CommandContext(ctx, c.Bin, "-p", prompt, "--append-system-prompt", SystemPrompt+cliRules,
			"--allowedTools", "Bash(hiro:*)")
	}
	cmd.Dir = c.Home
	cmd.Env = append(os.Environ(), "PATH="+bin+string(os.PathListSeparator)+os.Getenv("PATH"), "PYTHONUTF8=1")
	hideWindow(cmd)
	out, err := cmd.Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) && len(ee.Stderr) > 0 {
			msg := strings.TrimSpace(string(ee.Stderr))
			if len(msg) > 400 {
				msg = msg[len(msg)-400:]
			}
			return Reply{}, fmt.Errorf("%s 出错：%s", c.Kind, msg)
		}
		return Reply{}, fmt.Errorf("%s 出错：%w", c.Kind, err)
	}
	reply := strings.TrimSpace(string(out))
	if lastFile != "" {
		if b, err := os.ReadFile(lastFile); err == nil {
			reply = strings.TrimSpace(string(b))
		}
	}
	var cards []map[string]any
	after := c.pendingIDs(ctx)
	var ids []string
	for id := range after {
		if _, ok := before[id]; !ok {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	for _, id := range ids {
		p := after[id]
		cards = append(cards, map[string]any{"type": "confirm", "pending": p, "status": "waiting"})
	}
	return Reply{Text: reply, Cards: cards}, nil
}

// ---------------------------------------------------------------------------- 对话存储

// Threads 把对话存在 HIRO_HOME/threads/<编号>.json（界面原样存取，Go 不解析内容）。
type Threads struct {
	Home string `json:"-"`
}

var threadID = regexp.MustCompile(`^[A-Za-z0-9_\-]{1,64}$`)

func (t *Threads) dir() string { return filepath.Join(t.Home, "threads") }

// List 返回全部对话（新的在前）。
func (t *Threads) List() []json.RawMessage {
	entries, _ := os.ReadDir(t.dir())
	type item struct {
		mod time.Time
		raw json.RawMessage
	}
	var items []item
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		b, err := os.ReadFile(filepath.Join(t.dir(), e.Name()))
		if err != nil || !json.Valid(b) {
			continue
		}
		info, _ := e.Info()
		items = append(items, item{info.ModTime(), b})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].mod.After(items[j].mod) })
	out := []json.RawMessage{}
	for _, it := range items {
		out = append(out, it.raw)
	}
	return out
}

// Save 存一个对话。
func (t *Threads) Save(id string, thread json.RawMessage) error {
	if !threadID.MatchString(id) {
		return errors.New("对话编号不合法")
	}
	if !json.Valid(thread) {
		return errors.New("对话内容不是 JSON")
	}
	if err := os.MkdirAll(t.dir(), 0o755); err != nil {
		return err
	}
	tmp := filepath.Join(t.dir(), id+".json.tmp")
	if err := os.WriteFile(tmp, thread, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(t.dir(), id+".json"))
}

// Delete 删一个对话。
func (t *Threads) Delete(id string) error {
	if !threadID.MatchString(id) {
		return errors.New("对话编号不合法")
	}
	err := os.Remove(filepath.Join(t.dir(), id+".json"))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}
