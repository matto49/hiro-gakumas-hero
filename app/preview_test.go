package main

import (
	"context"
	"encoding/json"
	"image/png"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/shell"
)

// 用假数据离屏画主要画面（设计稿 §10）：设了 HIRO_PREVIEW_OUT 才画，PNG 不进仓库。
// 想要卡面图时设 HIRO_PREVIEW_ASSETS（目录里放 <asset>.webp / .png / .jpg，以及 frame.jpg）。
// 例：HIRO_PREVIEW_OUT=.preview go test -run TestPreview .
func TestPreview(t *testing.T) {
	out := os.Getenv("HIRO_PREVIEW_OUT")
	if out == "" {
		t.Skip("没设 HIRO_PREVIEW_OUT")
	}
	assets := os.Getenv("HIRO_PREVIEW_ASSETS")
	sprite := func(asset string) string {
		for _, ext := range []string{".webp", ".png", ".jpg"} {
			if p := filepath.Join(assets, asset+ext); assets != "" && exists(p) {
				return p
			}
		}
		return ""
	}
	frame := ""
	if assets != "" {
		frame = filepath.Join(assets, "frame.jpg")
	}
	live := false
	t0 := time.Now().Add(-12 * time.Minute).Unix()
	resp := func(args []string) string {
		key := strings.Join(args, " ")
		switch {
		case strings.HasPrefix(key, "catalog lookup"):
			return `{"items":{"i_card-skin-ttmr-3-0012":{"kind":"idols","name":"【Luna say maybe】月村手毬","rarity":"SSR","asset":"ttmr","attr":"Vo"}}}`
		case strings.HasPrefix(key, "catalog sprite"):
			b, _ := json.Marshal(map[string]string{"path": sprite(args[2])})
			return string(b)
		case key == "plan show":
			return `{"created":"2026-11-23 20:00","idol":{"id":"i_card-skin-ttmr-3-0012","name":"月村手毬"},"main_attr":"Vo","support":{"set":2,"own":[{"id":"s_card-2-0001","name":"一緒に行こう！","attr":"Vo","level":50,"why":["Vo 主力"]},{"id":"s_card-3-0012","name":"放課後の約束","attr":"Vo","level":50,"why":["集中"]},{"id":"s_card-3-0027","name":"止まらないドキドキ","attr":"Da","level":45,"why":[]},{"id":"s_card-3-0044","name":"集中タイム","attr":"Vo","level":60,"why":[]},{"id":"s_card-3-0070","name":"光に向かって","attr":"Vi","level":50,"why":[]}],"rental":{"id":"s_card-3-0000","name":"あなたにも作ってあげる！","attr":"Vo","estimated":true}},"memory":{"set":1,"chosen":[{"name":"アイドル宣言","upgrade":1,"timing":"start"},{"name":"成長痛","upgrade":0,"timing":"mid"},{"name":"仕切り直し","upgrade":1,"timing":"mid"},{"name":"輝くキミへ","upgrade":1,"timing":"final"}]},"notes":["主属性按初定 Vo"]}`
		case key == "run status":
			if live {
				return `{"current":{"id":"j7","kind":"hajime","state":"running","summary":"初 · 月村手毬","started_at":` + strconv.FormatInt(t0, 10) + `},"recent":[]}`
			}
			return `{"current":null,"recent":[{"id":"j5","kind":"hajime","state":"done","summary":"初 · 月村手毬","started_at":1763900000,"params":{"idol":"i_card-skin-ttmr-3-0012"}}]}`
		case strings.HasPrefix(key, "run events"):
			ev := []map[string]any{}
			steps := [][3]string{{"EVENT_CHOICE", "rule", "选第 2 个选项（体力 +）"}, {"SELECT_DRINK", "deepseek", "画面没认出来，问了一下 LLM"}, {"HIF_EXAM", "gnrpa", "出牌 6 回合，得分 4,820"}, {"SELECT_CARD", "gnrpa", "拿「アイドル宣言」，集中流核心"}, {"ACTION_SELECT", "gnrpa", "声乐课「レッスン」：声乐离上限还差 210"}}
			for i, s := range steps {
				ev = append(ev, map[string]any{"seq": i, "t": t0 + int64(i*150), "type": "step", "step": i + 1, "page": s[0], "source": s[1], "text": s[2], "frame": frame})
			}
			b, _ := json.Marshal(map[string]any{"state": "running", "events": ev})
			return string(b)
		case key == "info summary":
			return `{"account":{"plv":52,"ap":120,"ap_max":150},"kinds":{"supports":{"count":88,"scanned_at":"2026-11-20 21:10:00"},"memories":{"count":40,"scanned_at":"2026-11-18 20:00:00"},"idols":{"count":30,"scanned_at":"2026-11-22 10:00:00"}}}`
		case key == "confirm p1":
			live = true
			return `{"job":{"id":"j7","kind":"hajime"}}`
		case strings.HasPrefix(key, "usage daily"):
			return `{"rows":[{"key":"2026-11-25","calls":41,"total":612000,"cost":{"CNY":0.38}}],"totals":{"calls":41,"total":612000,"cost":{"CNY":0.38}},"prices_checked":"2026-11-25"}`
		case strings.HasPrefix(key, "usage monthly"):
			return `{"rows":[{"key":"2026-11","calls":412,"total":6120000,"cost":{"CNY":3.24},"subscription_usd":0.41,"models":[{"provider":"deepseek","model":"deepseek-flash","calls":398,"total":6010000,"cost":{"CNY":3.24}},{"provider":"claude-cli","model":"claude-sonnet-5-5","calls":14,"total":110000,"cost":{},"subscription_usd":0.41}]}],"totals":{"calls":412,"total":6120000,"cost":{"CNY":3.24},"subscription_usd":0.41},"prices_checked":"2026-11-25"}`
		case strings.HasPrefix(key, "usage recent"):
			return `{"jobs":[{"job":"20261125-071500-aaaa","kind":"hajime","summary":"初 · 篠澤広","calls":14,"total":208000,"cost":{"CNY":0.13}},{"job":"20261125-062900-bbbb","kind":"hajime","summary":"初 · 月村手毬","calls":9,"total":120000,"cost":{"CNY":0.08}},{"job":"20261124-220000-cccc","kind":"hif","summary":"H.I.F · 篠澤広","calls":31,"total":402000,"cost":{"CNY":0.27}}],"prices_checked":"2026-11-25"}`
		case strings.HasPrefix(key, "usage job"):
			return `{"job":"20261125-071500-aaaa","calls":14,"total":208000,"cost":{"CNY":0.13},"by_source":{"decision":{"calls":12,"total":190000,"cost":{"CNY":0.12}},"rescue":{"calls":2,"total":18000,"cost":{"CNY":0.01}}},"by_model":[{"provider":"deepseek","model":"deepseek-flash","calls":14,"total":208000,"cost":{"CNY":0.13}}],"items":[{"ts":"2026-11-25T07:16:02.000+08:00","source":"decision","provider":"deepseek","model":"deepseek-flash","tokens":15200,"cost":0.0091,"currency":"CNY","kind":"priced","ok":true},{"ts":"2026-11-25T07:19:40.000+08:00","source":"rescue","provider":"deepseek","model":"deepseek-flash","tokens":9100,"cost":0.0052,"currency":"CNY","kind":"priced","ok":true}],"prices_checked":"2026-11-25"}`
		case strings.HasPrefix(key, "doctor"):
			return `{"items":[{"id":"platform","ok":true,"detail":"学マス（PlayCover）：已连上"}]}`
		}
		return `{}`
	}
	call := func(_ context.Context, args []string) hiroapi.Result {
		return hiroapi.Result{OK: true, Data: json.RawMessage(resp(args))}
	}
	home := t.TempDir()
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "model-keys.json")}}
	_ = a.Save(Settings{Provider: "deepseek", Model: "deepseek-flash", WizardDone: true, OpenIn: "chat"}, "sk-preview-not-real")
	run := &shell.SyncRunner{}
	app := buildApp(appDeps{Home: home, Call: call, Assistant: a, Bootstrap: &Bootstrap{Home: home, Hiro: &Hiro{}}, Run: run,
		Ready: func(context.Context) bool { return true }})
	// 一句话开局：agent 回偶像卡 + 待确认
	idol := map[string]any{"type": "info", "title": "偶像卡", "kind": "idols", "scannedAt": "2026-11-22 10:00:00",
		"items": []any{map[string]any{"id": "i_card-skin-ttmr-3-0012", "name": "【Luna say maybe】月村手毬", "title": "Luna say maybe", "idol": "月村手毬", "rarity": "SSR", "plan": "sense"}}}
	// 和 CLI 实际返回的一样：command 只是命令名，种类和参数在 args 里
	confirm := shell.ConfirmCard(hiroapi.Pending{ID: "p1", Command: "run start", Summary: "初", ExpiresAt: 4102444800,
		Args: map[string]any{"kind": "hajime", "idol": "i_card-skin-ttmr-3-0012", "difficulty": nil, "support_set": nil, "no_llm": false}})
	tt := ui.NewTester(app.View, 1280, 800)
	tt.SetScale(2)
	settle := func() {
		for i := 0; i < 8; i++ {
			tt.Frame()
			if run.Drain() == 0 {
				break
			}
		}
		tt.Frame()
	}
	settle()
	app.NewThread()
	settle()
	settle()
	save0 := func() {
		_ = os.MkdirAll(out, 0o755)
		f, err := os.Create(filepath.Join(out, "0-welcome.png"))
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		_ = png.Encode(f, tt.Image())
	}
	save0()
	app.Post("", nil)
	app.Current().Messages = nil
	app.Current().Messages = append(app.Current().Messages,
		&shell.Message{ID: "m1", Role: "user", Text: "帮我用月村手毬打一把初", Cards: nil},
		&shell.Message{ID: "m2", Role: "agent", Text: "找到了。初默认打普通难度，编成沿用你上次的方案，下面是要开的这一局：", Tools: []string{"info_idols · 1 条"}, Cards: []map[string]any{idol, confirm}})
	settle()
	save := func(name string) {
		_ = os.MkdirAll(out, 0o755)
		f, err := os.Create(filepath.Join(out, name+".png"))
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		if err := png.Encode(f, tt.Image()); err != nil {
			t.Fatal(err)
		}
	}
	save("1-confirm")
	// 选偶像卡：agent 出的选择卡（11-25 review）
	choiceMsg := &shell.Message{ID: "m2b", Role: "agent", Text: "手毬有两张 SSR，你要用哪一张？", Cards: []map[string]any{{"type": "choice", "kind": "idols", "question": "要用哪张手毬？", "status": "waiting",
		"options": []any{map[string]any{"id": "i_card-skin-ttmr-3-0012", "name": "【Luna say maybe】月村手毬", "title": "Luna say maybe", "idol": "月村手毬", "rarity": "SSR", "plan": "sense"},
			map[string]any{"id": "i_card-skin-ttmr-3-0099", "name": "【アイヴイ】月村手毬", "title": "アイヴイ", "idol": "月村手毬", "rarity": "SSR", "plan": "logic"}}}}}
	keep := app.Current().Messages
	app.Current().Messages = []*shell.Message{keep[0], choiceMsg}
	settle()
	save("1b-choice")
	app.Current().Messages = keep
	settle()
	if err := tt.Click("开始"); err != nil {
		t.Fatal(err)
	}
	settle()
	app.PollJobs()
	settle()
	save("2-running")
	app.OpenPanel("library", "")
	settle()
	save("3-library")
	app.OpenPanel("plan", "")
	settle()
	save("4-plan")
	app.OpenPanel("settings", "")
	settle()
	save("5-settings")
	app.OpenPanel("usage", "")
	settle()
	save("6-usage")
	app.OpenPanel("usage", "job/20261125-071500-aaaa")
	settle()
	save("7-usage-job")
	app.ClosePanel()
	var usageCard map[string]any
	_ = json.Unmarshal([]byte(resp([]string{"usage", "job", "20261125-071500-aaaa"})), &usageCard)
	app.Post("", plugin.Card{"type": "usage-job", "jobId": "20261125-071500-aaaa", "usage": usageCard})
	settle()
	save("8-usage-card")
}
