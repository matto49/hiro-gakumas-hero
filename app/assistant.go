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
)

// 模型配置和对话入口（设计稿 §8）：照 magpie 的「服务商 + Key + 模型」。
//
//	API 服务商（Presets：DeepSeek、Kimi、通义千问、智谱 GLM、OpenAI、OpenRouter、自定义）
//	          都走 OpenAI 兼容接口：填 API Key（按服务商分别存进数据目录的 model-keys.json，0600；界面只显示「已保存」）→ /models 列模型 → 选一个；
//	          右侧对话走内置的工具调用循环（Agent），对局里规则拿不准时的决策也用它（HIRO_LLM=openai:…）
//	codex     用户已登录的 codex 命令行，当外部 agent 跑：读 hiro 的说明、自己调 hiro 命令（设置页「高级」里）
//	claude    用户已登录的 claude 命令行，同上
//
// 命令行模式只读：它们只能查信息；要开局时在回答里写 HIRO_ACTION 行，由程序发起、拿到待确认后出确认卡。
// hiro 命令行在 HIRO_AGENT_SANDBOX 下另外拒绝 confirm / secret / run start。

// Preset 是一家 OpenAI 兼容的 API 服务商。名称在界面的消息目录里（providerXxx），这里只放地址。
type Preset struct {
	ID           string `json:"id"`
	BaseURL      string `json:"base_url"`      // 自定义为空，要用户填
	DefaultModel string `json:"default_model"` // 只有 DeepSeek 有缺省模型；其他家从 /models 里选
	KeyURL       string `json:"key_url"`       // 去哪里创建 Key（设置页提示用）
}

// Presets 是设置页可选的 API 服务商（顺序即界面顺序）。
var Presets = []Preset{
	{ID: "deepseek", BaseURL: DefaultDeepSeekURL, DefaultModel: DefaultDeepSeekModel, KeyURL: "https://platform.deepseek.com/api_keys"},
	{ID: "kimi", BaseURL: "https://api.moonshot.cn/v1", KeyURL: "https://platform.moonshot.cn/console/api-keys"},
	{ID: "qwen", BaseURL: "https://dashscope.aliyuncs.com/compatible-mode/v1", KeyURL: "https://bailian.console.aliyun.com/?apiKey=1"},
	{ID: "glm", BaseURL: "https://open.bigmodel.cn/api/paas/v4", KeyURL: "https://open.bigmodel.cn/usercenter/apikeys"},
	{ID: "openai", BaseURL: "https://api.openai.com/v1", KeyURL: "https://platform.openai.com/api-keys"},
	{ID: "openrouter", BaseURL: "https://openrouter.ai/api/v1", KeyURL: "https://openrouter.ai/settings/keys"},
	{ID: "custom"},
}

func preset(id string) (Preset, bool) {
	for _, p := range Presets {
		if p.ID == id {
			return p, true
		}
	}
	return Preset{}, false
}

// isAPI 是走 API Key 的服务商（不是命令行、不是「不用模型」）。
func isAPI(id string) bool {
	_, ok := preset(id)
	return ok
}

// keyName 是这家服务商的 Key 在 model-keys.json 里的名字。
func keyName(id string) string { return id + "-api-key" }

// Settings 是模型设置（不含 Key）。
type Settings struct {
	Provider string `json:"provider"`           // Presets 里的一家（缺省 deepseek）/ codex / claude / none（不用模型）
	Model    string `json:"model,omitempty"`    // 用哪个模型
	BaseURL  string `json:"base_url,omitempty"` // 自定义服务商的地址；预设的服务商留空用预设地址
	HasKey   bool   `json:"has_key"`            // 当前服务商有没有存 Key
	// HasKeys：每家服务商有没有存 Key（切换服务商时界面显示「已保存」用）。只在给界面的结果里有，不写进文件。
	HasKeys map[string]bool `json:"has_keys,omitempty"`
	// WizardDone：新手向导走完了（或跳过了），之后打开直接进主窗口。
	WizardDone bool `json:"wizard_done,omitempty"`
}

// DefaultDeepSeek 是 DeepSeek 的缺省地址和模型。
const (
	DefaultDeepSeekURL   = "https://api.deepseek.com"
	DefaultDeepSeekModel = "deepseek-flash" // 2026-11-17 /models 只列 deepseek-flash、deepseek-v4-pro
)

var errNoKey = errors.New("没有保存 Key")

// legacyDeepSeek：没填或旧名字。deepseek-chat / deepseek-reasoner 现在都由 deepseek-flash 应答，统一写成 flash。
var legacyDeepSeek = map[string]bool{"": true, "deepseek-chat": true, "deepseek-reasoner": true}

// endpoint 是这家服务商实际用的地址：自定义用填的，预设的用预设地址（旧设置里 DeepSeek 填过的地址照用）。
func endpoint(id, baseURL string) string {
	p, _ := preset(id)
	if id == "custom" || (baseURL != "" && id == "deepseek") {
		return strings.TrimRight(strings.TrimSpace(baseURL), "/")
	}
	return p.BaseURL
}

// Models 列出这家服务商这个 Key 能用的模型（GET /models），设置页的下拉框用。
// key 为空时用已存的；baseURL 只对自定义服务商有用。
func (a *Assistant) Models(ctx context.Context, provider, baseURL, key string) ([]string, error) {
	if !isAPI(provider) {
		return nil, errors.New("这家不用 API Key")
	}
	if key = strings.TrimSpace(key); key == "" {
		k, err := a.Keys.Get(keyName(provider))
		if err != nil || k == "" {
			return nil, errors.New("还没填 API Key")
		}
		key = k
	}
	url := endpoint(provider, baseURL)
	if url == "" {
		return nil, errors.New("还没填接口地址")
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", url+"/models", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+key)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("连不上服务商：%w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		return nil, ErrUnauthorized
	}
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("服务商出错（%d）", resp.StatusCode)
	}
	var out struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, errors.New("模型列表看不懂")
	}
	ids := []string{}
	for _, m := range out.Data {
		if m.ID != "" {
			ids = append(ids, m.ID)
		}
	}
	sort.Strings(ids)
	return ids, nil
}

// Providers 是设置页的服务商列表（预设地址、缺省模型、创建 Key 的地址）。
func (a *Assistant) Providers() []Preset { return Presets }

// KeyStore 存模型的 API Key。
type KeyStore interface {
	Get(name string) (string, error)
	Set(name, value string) error
	Delete(name string) error
}

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
	if s.Provider == "" {
		s.Provider = "deepseek" // 缺省用 DeepSeek
	}
	if s.Provider == "deepseek" && legacyDeepSeek[s.Model] {
		s.Model = DefaultDeepSeekModel
	}
	s.HasKeys = map[string]bool{}
	for _, p := range Presets {
		if k, err := a.Keys.Get(keyName(p.ID)); err == nil && k != "" {
			s.HasKeys[p.ID] = true
		}
	}
	s.HasKey = s.HasKeys[s.Provider]
	return s
}

// apiConfig 是当前 API 服务商的地址、模型和 Key；没配好返回说明。
func (a *Assistant) apiConfig() (url, model, key string, err error) {
	s := a.Settings()
	if !isAPI(s.Provider) {
		return "", "", "", errors.New("还没配模型：到设置里选服务商")
	}
	key, kerr := a.Keys.Get(keyName(s.Provider))
	if kerr != nil || key == "" {
		return "", "", "", errors.New("还没填 API Key：到设置里填")
	}
	url, model = endpoint(s.Provider, s.BaseURL), s.Model
	if p, _ := preset(s.Provider); model == "" {
		model = p.DefaultModel
	}
	switch {
	case url == "":
		return "", "", "", errors.New("还没填接口地址：到设置里填")
	case model == "":
		return "", "", "", errors.New("还没选模型：到设置里选")
	}
	return url, model, key, nil
}

// jobEnv 是开局时交给工具层的模型设置（对局里的 LLM 跟着这里走，hiro.brain.llm 读这些变量）：
// API 服务商配好了 → OpenAI 兼容接口；Claude / Codex → 本机命令行；不用模型或没配好 → off（只用规则和算法）。
func (a *Assistant) jobEnv() []string {
	s := a.Settings()
	switch s.Provider {
	case "claude":
		return []string{"HIRO_LLM=claude:sonnet"}
	case "codex":
		return []string{"HIRO_LLM=codex"}
	}
	if url, model, key, err := a.apiConfig(); err == nil {
		return []string{"HIRO_LLM=openai:" + model, "OPENAI_BASE_URL=" + url, "OPENAI_API_KEY=" + key}
	}
	return []string{"HIRO_LLM=off"}
}

// Save 保存模型设置；key 非空时存进这家服务商的 Key（model-keys.json；空的不动已存的）。
func (a *Assistant) Save(s Settings, key string) error {
	if s.Provider != "" && !isAPI(s.Provider) && s.Provider != "codex" && s.Provider != "claude" && s.Provider != "none" {
		return fmt.Errorf("不认识的服务商 %q", s.Provider)
	}
	if s.Provider == "" {
		s.Provider = "deepseek"
	}
	if k := strings.TrimSpace(key); k != "" {
		if !isAPI(s.Provider) {
			return errors.New("这家不用 API Key")
		}
		if err := a.Keys.Set(keyName(s.Provider), k); err != nil {
			return fmt.Errorf("Key 存不进本机文件：%w", err)
		}
	}
	if s.Provider != "custom" && s.Provider != "deepseek" {
		s.BaseURL = "" // 预设地址不写进文件：以后预设改了跟着改
	}
	// has_key 只记「有没有」（给 hiro doctor 看），Key 本身只在 model-keys.json 里
	s.HasKey = false
	if isAPI(s.Provider) {
		k, err := a.Keys.Get(keyName(s.Provider))
		s.HasKey = err == nil && k != ""
	}
	s.HasKeys = nil
	b, _ := json.MarshalIndent(s, "", "  ")
	if err := os.MkdirAll(a.Home, 0o755); err != nil {
		return err
	}
	return os.WriteFile(a.settingsPath(), b, 0o600)
}

func (a *Assistant) provider() (ChatProvider, error) {
	url, model, key, err := a.apiConfig()
	if err != nil {
		return nil, err
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

// Ask 让初星助手回答一句话（history 是之前的对话）。
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

工具：在终端里运行只读的 hiro 命令查信息，例如 hiro info summary、hiro info supports --rarity SSR、hiro catalog search 好調、hiro run status、hiro tools。每条命令打印一行 JSON。
你不能开局、扫描、确认或碰账号密码：hiro run start、hiro confirm、hiro secret … 都会被拒绝。
要开局或扫描时，在回答最后单独写一行：
HIRO_ACTION: run start <种类> [参数]
例如 HIRO_ACTION: run start daily、HIRO_ACTION: run start hif --command new --mode qualify、HIRO_ACTION: run start tower --idol 篠澤広 --layers 1。
程序会替你发起，并给玩家一张确认卡，玩家点了才开始。一次回答最多写一行。只回答玩家，不要改任何文件。`

// cliAllowed 是命令行 agent 能跑的只读命令（Claude 的权限白名单）；cliDenied 明确拒绝（压过用户自己设置里更宽的规则）。
var (
	cliAllowed = []string{"Bash(hiro info:*)", "Bash(hiro catalog:*)", "Bash(hiro run status:*)", "Bash(hiro run events:*)",
		"Bash(hiro pending list:*)", "Bash(hiro tools:*)", "Bash(hiro doctor)"}
	cliDenied = []string{"Bash(hiro confirm:*)", "Bash(hiro cancel:*)", "Bash(hiro secret:*)", "Bash(hiro run start:*)",
		"Bash(hiro run stop:*)", "Bash(hiro run pause:*)", "Bash(hiro run resume:*)", "Bash(hiro run takeover:*)", "Bash(pbpaste:*)"}
)

// actionLine 是 agent 请求开局的那一行。
var actionLine = regexp.MustCompile(`(?m)^\s*HIRO_ACTION:\s*(.+?)\s*$`)

func (c CLIAgent) prompt(history []Turn, text string) string {
	var b strings.Builder
	if len(history) > 0 {
		b.WriteString("之前的对话：\n")
		for _, h := range history {
			who := "玩家"
			if h.Role == "assistant" {
				who = "初星助手"
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

// argv 是命令行 agent 的参数：Claude 只给只读命令白名单、不读用户自己的设置；Codex 用只读沙箱（不能写文件、不能联网）。
func (c CLIAgent) argv(prompt, lastFile string) []string {
	if c.Kind == "codex" {
		return []string{c.Bin, "exec", "--skip-git-repo-check", "-s", "read-only", "-C", c.Home, "--output-last-message", lastFile,
			SystemPrompt + cliRules + "\n\n" + prompt}
	}
	argv := []string{c.Bin, "-p", prompt, "--append-system-prompt", SystemPrompt + cliRules,
		"--permission-mode", "default", "--setting-sources", "project"}
	argv = append(argv, "--allowedTools")
	argv = append(argv, cliAllowed...)
	argv = append(argv, "--disallowedTools")
	return append(argv, cliDenied...)
}

// status 把当前任务写成一句话放进提示词（Codex 的只读沙箱连不上本机服务）。
func (c CLIAgent) status(ctx context.Context) string {
	r := c.Hiro.Call(ctx, []string{"run", "status"})
	var d struct {
		Current *struct {
			Summary string `json:"summary"`
			State   string `json:"state"`
		} `json:"current"`
	}
	if !r.OK || json.Unmarshal(r.Data, &d) != nil {
		return ""
	}
	if d.Current == nil {
		return "（现在没有在跑的任务）\n"
	}
	return "（现在在跑：" + d.Current.Summary + "，状态 " + d.Current.State + "）\n"
}

func (c CLIAgent) Ask(ctx context.Context, history []Turn, text string) (Reply, error) {
	bin, err := c.shim()
	if err != nil {
		return Reply{}, err
	}
	prompt := c.status(ctx) + c.prompt(history, text)
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	lastFile := filepath.Join(c.Home, "codex-last.txt")
	_ = os.Remove(lastFile)
	argv := c.argv(prompt, lastFile)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Dir = c.Home
	// HIRO_AGENT_SANDBOX：hiro 命令行在这个环境变量下拒绝开局、确认和账号密码（第二道拦截）
	cmd.Env = append(os.Environ(), "PATH="+bin+string(os.PathListSeparator)+os.Getenv("PATH"), "PYTHONUTF8=1", "HIRO_AGENT_SANDBOX=1")
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
	if c.Kind == "codex" {
		if b, err := os.ReadFile(lastFile); err == nil {
			reply = strings.TrimSpace(string(b))
		}
	}
	// 回答里的 HIRO_ACTION 行：只认 run start，由程序自己发起（拿到的是待确认），做成确认卡；行本身从回答里去掉
	var cards []map[string]any
	for _, m := range actionLine.FindAllStringSubmatch(reply, 2) {
		args := strings.Fields(m[1])
		if len(args) < 3 || args[0] != "run" || args[1] != "start" {
			continue
		}
		r := c.Hiro.Call(ctx, args)
		switch {
		case r.Pending != nil:
			cards = append(cards, map[string]any{"type": "confirm", "pending": r.Pending, "status": "waiting"})
		case !r.OK && r.Error != nil:
			cards = append(cards, map[string]any{"type": "error", "error": r.Error})
		}
	}
	reply = strings.TrimSpace(actionLine.ReplaceAllString(reply, ""))
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
