// Package usagelog 把桌面程序里的模型调用（对话助手）记进流水账，格式同 Python hiro.usage.ledger：
// $HIRO_HOME/usage/YYYY-MM.jsonl，一次调用一行。不写 Key、提示词、回复正文。
package usagelog

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Version 是行格式版本（同 Python ledger.VERSION）。
const Version = 1

// Row 是一次调用。指针字段为 nil 表示「不知道」（写 null，不当 0）。
type Row struct {
	Source     string // chat / decision / rescue …
	Job        string
	Thread     string
	Provider   string
	Model      string
	Input      *int // 不含缓存命中的输入
	Output     *int
	CacheRead  *int
	CacheWrite *int
	Cost       *float64 // 服务方报的花费（claude 的 total_cost_usd）
	Currency   string
	OK         bool
}

type line struct {
	V          int      `json:"v"`
	ID         string   `json:"id"`
	TS         string   `json:"ts"`
	Source     string   `json:"source"`
	Job        *string  `json:"job"`
	Thread     *string  `json:"thread"`
	Provider   string   `json:"provider"`
	Model      *string  `json:"model"`
	Input      *int     `json:"input"`
	Output     *int     `json:"output"`
	CacheRead  *int     `json:"cache_read"`
	CacheWrite *int     `json:"cache_write"`
	Cost       *float64 `json:"cost"`
	Currency   *string  `json:"currency"`
	CostSource *string  `json:"cost_source"`
	OK         bool     `json:"ok"`
}

// hosts：OpenAI 兼容接口的域名 → 服务商 id（同 Python ledger.HOSTS、assistant.go 的 Presets）。
var hosts = map[string]string{
	"api.deepseek.com":       "deepseek",
	"api.moonshot.cn":        "kimi",
	"dashscope.aliyuncs.com": "qwen",
	"open.bigmodel.cn":       "glm",
	"api.openai.com":         "openai",
	"openrouter.ai":          "openrouter",
}

// aliases：这些旧名字现在都由 deepseek-flash 应答、按 Flash 计费（官方价格页；同 Python ledger.ALIASES）。
var aliases = map[[2]string]string{
	{"deepseek", "deepseek-chat"}:                "deepseek-flash",
	{"deepseek", "deepseek-reasoner"}:            "deepseek-flash",
	{"deepseek", "deepseek-v4-flash"}:            "deepseek-flash",
	{"deepseek", "deepseek-v4-flash-vision-exp"}: "deepseek-flash",
}

// ProviderOf 由接口地址反推服务商 id；认不出 → openai-compatible。
func ProviderOf(baseURL string) string {
	u, err := url.Parse(strings.TrimSpace(baseURL))
	if err == nil {
		if p, ok := hosts[strings.ToLower(u.Hostname())]; ok {
			return p
		}
	}
	return "openai-compatible"
}

func ptr[T any](v T) *T { return &v }

func strOrNil(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// Record 追加一行。home 为空（没有 HIRO_HOME）时不记；写失败返回 error，不 panic，调用方只告警。
func Record(home string, r Row) error {
	if home == "" {
		return nil
	}
	now := time.Now()
	model := r.Model
	if m, ok := aliases[[2]string{r.Provider, model}]; ok {
		model = m
	}
	cw := r.CacheWrite
	if cw == nil && r.Input != nil {
		cw = ptr(0)
	}
	l := line{V: Version, ID: newID(), TS: now.Format("2006-01-02T15:04:05.000-07:00"), Source: r.Source,
		Job: strOrNil(r.Job), Thread: strOrNil(r.Thread), Provider: r.Provider, Model: strOrNil(model),
		Input: r.Input, Output: r.Output, CacheRead: r.CacheRead, CacheWrite: cw, Cost: r.Cost, OK: r.OK}
	if r.Cost != nil {
		cur := r.Currency
		if cur == "" {
			cur = "USD"
		}
		l.Currency, l.CostSource = &cur, ptr("reported")
	}
	b, err := json.Marshal(l)
	if err != nil {
		return err
	}
	dir := filepath.Join(home, "usage")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	// O_APPEND + 一次 Write：和 Python 任务子进程同时写同一个文件时行不交错
	f, err := os.OpenFile(filepath.Join(dir, now.Format("2006-01")+".jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	_, werr := f.Write(append(b, '\n'))
	if cerr := f.Close(); werr == nil {
		werr = cerr
	}
	return werr
}

type threadKey struct{}

// WithThread 把对话 id 放进 ctx（shell 发消息时放，记账时取）。
func WithThread(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, threadKey{}, id)
}

// ThreadOf 取 ctx 里的对话 id，没有就是空串。
func ThreadOf(ctx context.Context) string {
	s, _ := ctx.Value(threadKey{}).(string)
	return s
}
