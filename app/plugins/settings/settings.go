// Package settings 是内置的「设置」插件：模型、游戏账号、环境、插件管理；第一次使用的整窗流程；
// 侧栏底部的游戏连接和模型；游戏连接状态的上下文（设计稿 §8）。
package settings

import (
	"context"
	"strings"
	"sync"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

// Settings 和 main 里 Assistant 的 Settings 同形（settings.json）。
type Settings struct {
	Provider   string          `json:"provider"`
	Model      string          `json:"model,omitempty"`
	BaseURL    string          `json:"base_url,omitempty"`
	HasKey     bool            `json:"has_key"`
	HasKeys    map[string]bool `json:"has_keys,omitempty"`
	WizardDone bool            `json:"wizard_done,omitempty"`
	WizardStep int             `json:"wizard_step,omitempty"`
	// Permission 是操作权限档（ask / read / readwrite / all，空 = read）；AllowAllConfirmed 是第一次切到 all 时确认过。
	Permission        string `json:"permission,omitempty"`
	AllowAllConfirmed bool   `json:"allow_all_confirmed,omitempty"`
	// ScanBeforeStart：开局前自动补扫回忆（设计 5.6，缺省关；hiro.memory_refresh 读 settings.json 的 scan_before_start）。
	ScanBeforeStart bool `json:"scan_before_start,omitempty"`
	// MemorySlicesOff：关掉了「回忆按片扫描」（筛选 + 入手排序、增量补扫、一键全收集、首次进入走一键全收集）。
	// D10（用户 11-26 18:14 定）缺省开：没写就是开，玩家关了才记 true。原来的 memory_slices（缺省关时勾上才写）不再读，开着照样是开。
	MemorySlicesOff bool `json:"memory_slices_off,omitempty"`
}

// permLevels 是「操作权限」的四档（11-25 review，参考 dsh）。
var permLevels = []struct{ id, name, desc string }{
	{"ask", "总是询问", "每件会动游戏的事都先出确认卡，你点了才做。"},
	{"read", "允许读操作", "只看画面的（扫描、检查设置、对照编成）直接做；领日常、往空白编成位写编成也直接做。开局、改已有编成还是会问。"},
	{"readwrite", "允许读写操作", "除了用掉培育次数的事（开局、接着打、初星课题），都直接做，包括改掉已有的编成。"},
	{"all", "允许所有行为", "开局、接着打也直接开始，不再出确认卡。花钻石、放弃培育这些不管哪一档都不会做。"},
}

// Preset 是一家 API 服务商。
type Preset struct {
	ID           string
	BaseURL      string
	DefaultModel string
	KeyURL       string
}

// BootStatus 是运行环境装没装好。
type BootStatus struct {
	Ready bool
	Steps []string
	Err   string
}

// Deps 是设置插件要的宿主能力（main 里接 Assistant / Bootstrap / Hiro.SetSecret）。
type Deps struct {
	Settings   func() Settings
	Save       func(s Settings, key string) error
	Providers  func() []Preset
	Models     func(ctx context.Context, provider, baseURL, key string) ([]string, error)
	Test       func(ctx context.Context) (bool, string)
	BootStatus func(ctx context.Context) BootStatus
	BootRun    func(ctx context.Context, onLine func(string)) error
	BootReset  func() error
	SetSecret  func(ctx context.Context, name, value string) hiroapi.Result
	// OnModelChanged 在模型设置保存后调用（shell 重新决定能不能直接说话）。
	OnModelChanged func()
	// OnWizardDone 在第一次使用的向导走完时调用（shell 自己扫一遍从没扫过的库存，R11）。
	OnWizardDone func()
	// PluginsPanel 画「管理插件」页（外部插件，见 extplugin）。
	PluginsPanel func(c *ui.Context, h plugin.Host)
	// RelaunchAdmin 以管理员身份重新打开初星助手（Windows：DMM 游戏是管理员运行时普通权限截不到画面，G3）；
	// 先关后台服务再提权重开、关掉当前窗口。nil 表示这个系统不需要（Mac）。
	RelaunchAdmin func() error
}

var providerName = map[string]string{
	"deepseek": "DeepSeek", "kimi": "Kimi", "qwen": "通义千问", "glm": "智谱 GLM", "openai": "OpenAI", "openrouter": "OpenRouter",
	"custom": "自定义（OpenAI 兼容）", "codex": "Codex 命令行", "claude": "Claude 命令行", "none": "不用模型",
}

// ProviderLabel 是服务商的中文名。
func ProviderLabel(id string) string {
	if n, ok := providerName[id]; ok {
		return n
	}
	return id
}

// S 是设置插件的状态。
type S struct {
	d Deps

	boot      *BootStatus
	bootKnown bool // 环境状态读回来过（之前什么都不画，免得启动时闪一下「第一次使用」）
	bootLines []string
	booting   bool
	step      int
	sound     bool
	// N27（G3 第 2 轮：Windows 从安装到开局点了 18 下）：这次刚装好环境 / 第一次走到「连游戏」并连上，就自己进下一步。
	// connectAuto：第一次停在「连游戏」这一步期间才自动前进；离开过（往前或往后）就不再自动跳（#154 review）
	installedNow bool
	connectSeen  bool
	connectAuto  bool
	modelOK      bool
	// relaunchErr 是「以管理员身份重新打开」没成的原因（取消了「是否允许」等）
	relaunchErr string
	// stepLoaded / savedStep：向导步数从设置里读过一次；上次存下的步数（变了才存）
	stepLoaded bool
	savedStep  int

	// 模型表单
	formFor  string // 表单是按哪次读到的设置填的
	provider string
	model    string
	baseURL  string
	key      string
	models   []string
	note     string
	noteBad  bool
	busy     bool

	// 账号密码
	secretVals map[string]string
	secretSet  map[string]bool
	secretNote string

	// 游戏连接（Context 在后台线程读，改和读都要拿 devMu）
	devMu    sync.Mutex
	device   string
	deviceOK *bool
	probedAt time.Time
	probing  bool
	doctor   map[string]doctorItem
}

type doctorItem struct {
	ID        string `json:"id"`
	Title     string `json:"title"`
	OK        *bool  `json:"ok"`
	Detail    string `json:"detail"`
	Next      string `json:"next"`
	NeedAdmin bool   `json:"need_admin"` // 游戏比初星助手权限高（Windows DMM 版），要以管理员身份重新打开
}

// New 建设置插件。
func New(d Deps) *S {
	return &S{d: d, secretVals: map[string]string{}, secretSet: map[string]bool{}}
}

// Plugin 是给注册表的插件。
func (s *S) Plugin() *plugin.Plugin {
	return &plugin.Plugin{
		ID:   "settings",
		Name: "设置",
		Icon: kit.IcSliders,
		Nav: []plugin.NavItem{
			{Label: "管理插件", Icon: kit.IcPuzzle, Route: "plugins"},
			{Label: "设置", Icon: kit.IcSliders, Route: ""},
		},
		Tools:        []string{"doctor"},
		Panel:        func(c *ui.Context, h plugin.Host, route string) { s.panel(c, h, route) },
		Footer:       func(c *ui.Context, h plugin.Host) { s.footer(c, h) },
		ContextTitle: "游戏",
		Context:      func(context.Context, plugin.Host) string { return s.deviceContext() },
	}
}

// ---- 游戏连接 ----

// Probe 试连一次游戏（doctor --probe，只读截一张图）；有任务在跑时不试（不和任务抢游戏）。
func (s *S) Probe(h plugin.Host) {
	if s.probing || h.LiveJob() != "" {
		return
	}
	s.probing = true
	s.devMu.Lock()
	s.device = "正在试连游戏…"
	s.devMu.Unlock()
	h.Go(func() func() {
		var d struct {
			Items []doctorItem `json:"items"`
		}
		err := h.Call(context.Background(), []string{"doctor", "--probe"}).Decode(&d)
		return func() {
			s.devMu.Lock()
			defer s.devMu.Unlock()
			s.probing = false
			s.probedAt = h.Now()
			ok := false
			s.device = "游戏未连接 · 点这里重试"
			if err != nil {
				s.deviceOK = &ok
				return
			}
			s.doctor = map[string]doctorItem{}
			for _, it := range d.Items {
				s.doctor[it.ID] = it
				if it.ID == "platform" && it.OK != nil && *it.OK {
					ok = true
					label, _, _ := strings.Cut(it.Detail, "：")
					s.device = label + " 已连上"
				}
			}
			s.deviceOK = &ok
			if ok && s.step == 1 && s.connectAuto && !s.d.Settings().WizardDone {
				// N27：向导第一次走到「连游戏」并连上：停一下让人看到「已连上」，再自己进下一步
				h.Go(func() func() {
					time.Sleep(connectedPause)
					return func() {
						if s.step == 1 && s.connectAuto {
							s.step = 2
						}
					}
				})
			}
		}
	})
}

// connectedPause 是向导里连上游戏后停留多久再自己进下一步（测试里调成 0）。
var connectedPause = 1200 * time.Millisecond

func (s *S) deviceContext() string {
	s.devMu.Lock()
	defer s.devMu.Unlock()
	if s.deviceOK == nil {
		return "还没试连过游戏。"
	}
	return s.device + "（" + s.probedAt.Format("01-02 15:04") + " 试连）"
}

func (s *S) footer(c *ui.Context, h plugin.Host) {
	if s.deviceOK == nil && !s.probing && s.probedAt.IsZero() && h.LiveJob() == "" {
		s.Probe(h)
	}
	dot, text := kit.Ink3, s.device
	switch {
	case h.LiveJob() != "":
		dot, text = kit.Brand, "任务进行中"
	case s.deviceOK != nil && *s.deviceOK:
		dot = kit.Good
	case s.deviceOK != nil:
		dot = kit.Bad
	case text == "":
		text = "还没试连游戏 · 点这里试连"
	}
	if kit.RowButton(c, "device", text, false).Padding(3, 0).Children(func() {
		ui.Box(c).Size(7, 7).Radius(4).Background(dot)
		ui.Text(c, text).FontSize(kit.FNote).TextColor(kit.Ink2).MaxLines(1).Ellipsis("…")
	}).Clicked() {
		s.Probe(h)
	}
	st := s.d.Settings()
	label := ProviderLabel(st.Provider)
	if st.Model != "" {
		label += " · " + st.Model
	}
	if needsKey(st.Provider) && !st.HasKey {
		// G3（11-26）：向导里跳过了 Key，这里还显示「DeepSeek · deepseek-flash」，看着像配好了
		label = "还没配模型 Key · 点这里设置"
	}
	if kit.RowButton(c, "model", label, false).Padding(3, 0).Children(func() {
		ui.Icon(c, kit.IcSparkles).FontSize(12).TextColor(kit.Ink3)
		ui.Text(c, label).FontSize(kit.FNote).TextColor(kit.Ink2).MaxLines(1).Ellipsis("…")
	}).Clicked() {
		h.OpenPanel("settings", "")
	}
}

// ---- 第一次使用 ----

// Recheck 重新读环境状态（装完、走完之后）。
func (s *S) Recheck(h plugin.Host) {
	h.Go(func() func() {
		st := s.d.BootStatus(context.Background())
		return func() { s.boot, s.bootKnown = &st, true }
	})
}

// Gate 在环境没装好或第一次使用没走完时画整窗的「第一次使用」，返回 true；否则什么都不画、返回 false。
func (s *S) Gate(c *ui.Context, h plugin.Host) bool {
	if s.boot == nil {
		s.boot = &BootStatus{Ready: false}
		s.Recheck(h)
	}
	if !s.bootKnown {
		ui.Box(c).Fill().Background(kit.Bg) // 还在检查：先空着
		return true
	}
	if s.boot.Ready && s.d.Settings().WizardDone {
		return false
	}
	s.wizard(c, h)
	return true
}

// N27（G3 第 3 轮 11-26：Windows 从安装到开局 15 次）：「账号密码」不放进第一次使用（掉登录时才用，到设置里填），省一次「跳过」
var steps = []string{"装环境", "连游戏", "关游戏声音", "配模型", "第一次扫描"}

func (s *S) wizard(c *ui.Context, h plugin.Host) {
	if !s.boot.Ready {
		s.step = 0
	} else {
		// G3（11-26）：以管理员身份重新打开后向导从第 1 步重来——步数存进设置，重开接着走
		if !s.stepLoaded {
			s.stepLoaded = true
			if n := min(s.d.Settings().WizardStep, len(steps)-1); n > 0 { // 旧版存的步数可能多一步（原来有「账号密码」）
				s.step = n
				s.connectSeen = n > 1 // 重开时已经过了「连游戏」：退回去看不算第一次停留，不自动前进（#154 review）
			}
			s.savedStep = s.step
		}
		if s.step == 0 && s.installedNow { // 刚装好：不用再点「下一步」
			s.installedNow = false
			s.step = 1
		}
		if s.step != s.savedStep {
			st := s.d.Settings()
			st.WizardStep = s.step
			if s.d.Save(st, "") == nil {
				s.savedStep = s.step
			}
		}
	}
	if s.step == 1 && !s.connectSeen {
		s.connectSeen, s.connectAuto = true, true
	} else if s.step != 1 {
		s.connectAuto = false
	}
	ui.Row(c).Fill().Background(kit.Bg).AlignItems(ui.Stretch).Children(func() {
		ui.Column(c).Width(232).Shrink(0).Background(kit.Side).Padding(22, 18).Gap(6).BorderColor(kit.Line).BorderWidth(0, kit.Hair, 0, 0).Children(func() {
			ui.Row(c).Gap(8).AlignItems(ui.Center).Padding(0, 0, 16, 0).Children(func() {
				kit.Logo(c, 24)
				ui.Text(c, "初星助手").FontSize(15).FontWeight(650).TextColor(kit.Ink)
			})
			ui.Text(c, "第一次使用 · 五步").FontSize(kit.FNote).TextColor(kit.Ink3).Padding(0, 0, 6, 0)
			for i, st := range steps {
				ui.Row(c).Gap(10).AlignItems(ui.Center).Padding(6, 8).Radius(kit.R8).Background(map[bool]ui.Color{true: kit.BrandT, false: ui.RGBA(0, 0, 0, 0)}[i == s.step]).Children(func() {
					bg, fg := kit.CardBg, kit.Ink3
					if i < s.step {
						bg, fg = kit.BrandT, kit.WarnInk
					} else if i == s.step {
						bg, fg = kit.Brand, kit.OnPrim
					}
					ui.Column(c).Size(22, 22).Radius(11).Background(bg).Border(kit.Hair, kit.LineStr).Center().Children(func() {
						n := []string{"1", "2", "3", "4", "5", "6"}[i]
						if i < s.step {
							n = "✓"
						}
						ui.Text(c, n).FontSize(11.5).FontWeight(600).TextColor(fg)
					})
					ui.Text(c, st).FontSize(kit.FSmall).TextColor(kit.Ink)
				})
			}
			ui.Spacer(c)
			kit.Note(c, "每一步以后都能在「设置」里改。会动游戏的操作都要你点确认。")
		})
		ui.Scroll(c).Grow(1).Padding(32, 40).Gap(14).Children(func() {
			ui.Column(c).MaxWidth(600).Gap(14).Children(func() {
				ui.Text(c, steps[s.step]).FontSize(21).FontWeight(700).TextColor(kit.Ink)
				switch s.step {
				case 0:
					s.envSection(c, h)
				case 1:
					// N5（G1 实测）：进这一步就自己试连一次，不用玩家先点「试连」
					if s.deviceOK == nil && !s.probing && s.probedAt.IsZero() {
						s.Probe(h)
					}
					kit.Note(c, "先把游戏打开、停在首页。Mac 用带 maaTools 的 PlayCover，并在游戏设置里打开 maaTools；Windows 用 DMM 版，窗口不要最小化。")
					s.deviceLine(c, h)
					if kit.Secondary(c, "试连").Disabled(s.probing).Clicked() {
						s.Probe(h)
					}
					kit.Note(c, "现在连不上也可以先往下走，之后在侧栏底部点状态行重试。")
				case 2:
					kit.Note(c, "初星助手打游戏时不出声：在游戏里打开设置 → 声音，把 BGM、语音、效果音都调到 0。不用动电脑的音量。")
					// N27：原来是勾「我在游戏里关好了」再点「下一步」两下，合成一下
					if kit.Primary(c, "关好了，下一步").Clicked() {
						s.sound = true
						s.step++
					}
				case 3:
					s.modelSection(c, h)
				case 4:
					kit.Note(c, "点「完成」后，初星助手会自己扫一遍还没扫过的支援卡、回忆和偶像卡（只看画面，不改东西），游戏停在首页就行。也可以现在单独点下面几项。")
					kit.Note(c, "游戏账号密码现在不用填：游戏掉登录时才用，到「设置」里填。")
					for _, sc := range [][2]string{{"scan-supports", "扫描支援卡"}, {"scan-memories", "扫描回忆"}, {"scan-account", "读首页数值"}, {"check-settings", "检查游戏设置"}} {
						if kit.Secondary(c, sc[1]).Clicked() {
							h.RunArgs(sc[1], []string{"run", "start", sc[0]})
						}
					}
				}
				// N5：模型这一步填了 Key（或已存过）就能点「下一步」：没测通时先存、再测，测通了自己进下一步
				modelReady := s.modelOK || strings.TrimSpace(s.key) != "" || s.d.Settings().HasKeys[s.provider] ||
					(s.d.Settings().HasKey && s.d.Settings().Provider == s.provider)
				canNext := []bool{s.boot.Ready, true, s.sound, modelReady && !s.busy, true}[s.step]
				ui.Row(c).Gap(8).Padding(10, 0, 0, 0).Children(func() {
					if s.step > 0 && kit.Secondary(c, "上一步").Clicked() {
						s.step--
					}
					if s.step == 3 && kit.Secondary(c, "跳过").Clicked() {
						s.step++
					}
					if s.step < len(steps)-1 && s.step != 2 { // 「关游戏声音」的「关好了，下一步」在正文里
						if kit.Primary(c, "下一步").Disabled(!canNext).Clicked() && canNext {
							if s.step == 3 && !s.modelOK {
								s.saveAndTest(h, true)
							} else {
								s.step++
							}
						}
					} else if s.step == len(steps)-1 && kit.Primary(c, "完成，去对话").Clicked() {
						st := s.d.Settings()
						st.WizardDone = true
						_ = s.d.Save(st, "")
						if s.d.OnModelChanged != nil {
							s.d.OnModelChanged()
						}
						if s.d.OnWizardDone != nil {
							s.d.OnWizardDone()
						}
					}
				})
			})
		})
	})
}

// permNote：操作权限在对话框左下角切换（11-25 review「这个设置，我们放进对话框里吧，ui 同 codex」），这里只写现在是哪一档。
func (s *S) permNote(c *ui.Context) {
	cur := s.d.Settings().Permission
	if cur == "" {
		cur = "read"
	}
	name := cur
	for _, lv := range permLevels {
		if lv.id == cur {
			name = lv.name
		}
	}
	kit.Note(c, "现在是「"+name+"」。在对话框左下角点它就能换，哪些事不用确认就直接做都写在那里。")
}

const (
	memorySlicesLabel    = "回忆按片扫描"
	scanBeforeStartLabel = "开局前自动补扫回忆（最多 3 分钟）"
)

// scanSection：回忆按片扫描（D10 缺省开）、开局前自动补扫（缺省关）两个开关（设计 5.6 / D4，#116 审查重要 5）。
func (s *S) scanSection(c *ui.Context) {
	st := s.d.Settings()
	slices := !st.MemorySlicesOff // D10：缺省开
	ui.Checkbox(c, &slices, memorySlicesLabel)
	kit.Note(c, "回忆按流派 × 模式分 12 片扫，只补扫新回忆；首次进入改走一键全收集。会临时改回忆列表的排序和筛选，扫完改回排序、筛选重置为默认。")
	before := st.ScanBeforeStart
	ui.Checkbox(c, &before, scanBeforeStartLabel)
	kit.Note(c, "要先开上面的「回忆按片扫描」。开新的一局前，看这局要用的回忆有没有超过 1 天没扫，有就增量补扫；超过 3 分钟先开局，打完再补。")
	if slices == st.MemorySlicesOff || before != st.ScanBeforeStart {
		st.MemorySlicesOff, st.ScanBeforeStart = !slices, before
		_ = s.d.Save(st, "")
	}
}

func (s *S) deviceLine(c *ui.Context, h plugin.Host) {
	text := s.device
	if text == "" {
		text = "还没试连"
	}
	col := kit.Ink2
	if s.deviceOK != nil && *s.deviceOK {
		col = kit.Good
	} else if s.deviceOK != nil {
		col = kit.Bad
	}
	ui.Text(c, text).FontSize(kit.FSmall).TextColor(col)
	if it, ok := s.doctor["platform"]; ok && it.OK != nil && !*it.OK {
		kit.Note(c, it.Detail+"　"+it.Next)
		if it.NeedAdmin && s.d.RelaunchAdmin != nil && h.LiveJob() == "" { // 有任务在跑时不给（重开会停掉它，#140 review）
			if kit.Secondary(c, "以管理员身份重新打开").Key("relaunch-admin").Label("以管理员身份重新打开").Clicked() {
				s.relaunchErr = ""
				if err := s.d.RelaunchAdmin(); err != nil {
					s.relaunchErr = err.Error()
				}
			}
			if s.relaunchErr != "" {
				kit.Note(c, s.relaunchErr)
			}
		}
	}
}

// ---- 环境 ----

func (s *S) envSection(c *ui.Context, h plugin.Host) {
	b := s.boot
	if b.Ready {
		ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
			ui.Icon(c, kit.IcCheck).FontSize(14).TextColor(kit.Good)
			ui.Text(c, "运行环境装好了").FontSize(kit.FSmall).TextColor(kit.Ink)
		})
		s.ocrLine(c)
		if kit.Secondary(c, "下载 / 更新资料库").Disabled(s.busy).Clicked() {
			s.busy = true
			h.Go(func() func() {
				r := h.Call(context.Background(), []string{"catalog", "sync"})
				return func() {
					s.busy = false
					s.setNote(r, "资料库下载好了")
				}
			})
		}
		s.noteLine(c)
		return
	}
	kit.Note(c, "第一次用要先装好初星助手的运行环境（不用你装 Python，全自动）：")
	for _, st := range b.Steps {
		ui.Text(c, "· "+st).FontSize(kit.FSmall).TextColor(kit.Ink2)
	}
	if b.Err != "" {
		ui.Text(c, b.Err).FontSize(kit.FNote).TextColor(kit.Bad).Wrap()
	}
	label := "安装"
	if s.booting {
		label = "安装中…"
	}
	if kit.Primary(c, label).Disabled(s.booting || b.Err != "").Clicked() {
		s.installEnv(h)
	}
	// 磁盘不够等错误是打开时算的：清完空间点这里重算，不用关掉重开（N25，G1 第 3 轮 11-26）
	if b.Err != "" && !s.booting && kit.Secondary(c, "重新检查").Key("boot-recheck").Clicked() {
		s.Recheck(h)
	}
	if n := len(s.bootLines); n > 0 {
		ui.Text(c, strings.Join(s.bootLines[max(0, n-30):], "\n")).Font(kit.Mono).FontSize(11).TextColor(kit.Ink2).Padding(10).Radius(kit.R8).Background(kit.Surface2).Wrap()
	}
}

// ocrLine 写现在用哪个文字识别后端（doctor 的 ocr 项，随试连游戏一起读到；还没读到时不画）。
// SIMD 没装好时用 RapidOCR，识别慢一些：告诉用户怎么再装一次。
func (s *S) ocrLine(c *ui.Context) {
	s.devMu.Lock()
	it, ok := s.doctor["ocr"]
	s.devMu.Unlock()
	if !ok {
		return
	}
	col := kit.Ink2
	if it.OK != nil && !*it.OK {
		col = kit.Bad
	}
	ui.Text(c, "文字识别："+it.Detail).FontSize(kit.FSmall).TextColor(col).Wrap()
	if it.Next != "" && it.OK != nil && !*it.OK {
		kit.Note(c, it.Next)
	}
}

func (s *S) installEnv(h plugin.Host) {
	s.booting = true
	s.bootLines = nil
	h.Go(func() func() {
		err := s.d.BootRun(context.Background(), func(line string) {
			h.Go(func() func() { return func() { s.bootLines = append(s.bootLines, line) } })
		})
		return func() {
			s.booting = false
			if err != nil {
				s.bootLines = append(s.bootLines, "出错了："+err.Error())
			} else {
				s.installedNow = true
			}
			s.Recheck(h)
			s.refreshOcr(h) // 装好 SIMD 之后，「文字识别」那行不能还是装之前读到的
		}
	})
}

// refreshOcr 重新读文字识别后端：调不连游戏的 doctor，只更新 ocr 项（platform 等项留着试连时读到的）。
// 不看 deviceOK，游戏试连过与否都刷。
func (s *S) refreshOcr(h plugin.Host) {
	h.Go(func() func() {
		var d struct {
			Items []doctorItem `json:"items"`
		}
		err := h.Call(context.Background(), []string{"doctor"}).Decode(&d)
		return func() {
			if err != nil {
				return
			}
			s.devMu.Lock()
			defer s.devMu.Unlock()
			for _, it := range d.Items {
				if it.ID == "ocr" {
					if s.doctor == nil {
						s.doctor = map[string]doctorItem{}
					}
					s.doctor[it.ID] = it
				}
			}
		}
	})
}

// ---- 模型 ----

func (s *S) fillForm() {
	st := s.d.Settings()
	key := st.Provider + "|" + st.Model + "|" + st.BaseURL
	if s.formFor == key {
		return
	}
	s.formFor = key
	s.provider, s.model, s.baseURL = st.Provider, st.Model, st.BaseURL
	if s.provider == "" {
		s.provider = "deepseek"
	}
}

func (s *S) preset(id string) (Preset, bool) {
	for _, p := range s.d.Providers() {
		if p.ID == id {
			return p, true
		}
	}
	return Preset{}, false
}

func (s *S) modelSection(c *ui.Context, h plugin.Host) {
	s.fillForm()
	kit.Section(c, "服务商")
	ids := []string{}
	for _, p := range s.d.Providers() {
		ids = append(ids, p.ID)
	}
	ids = append(ids, "codex", "claude", "none")
	ui.Row(c).Gap(6).Wrap().Children(func() {
		for _, id := range ids {
			b := ui.Button(c, ProviderLabel(id)).Key("prov-"+id).FontSize(kit.FNote).Padding(4, 10).Radius(99)
			if id == s.provider {
				b = b.Background(kit.BrandT).TextColor(kit.WarnInk).Border(kit.Hair, kit.Warn)
			} else {
				b = b.Background(kit.CardBg).TextColor(kit.Ink2).Border(kit.Hair, kit.Line)
			}
			if b.Clicked() {
				s.provider, s.models = id, nil
				if p, ok := s.preset(id); ok {
					s.model = p.DefaultModel
				}
			}
		}
	})
	p, api := s.preset(s.provider)
	switch {
	case s.provider == "codex" || s.provider == "claude":
		kit.Note(c, "用你电脑上已经登录好的 "+ProviderLabel(s.provider)+"。它不能替你确认开局。")
	case s.provider == "none":
		kit.Note(c, "不用模型时只能点快捷按钮和用「/」命令。")
	case api:
		if s.provider == "custom" {
			kit.Section(c, "地址")
			ui.TextInput(c, &s.baseURL).Placeholder("https://…/v1").FontSize(kit.FSmall)
		}
		kit.Section(c, "模型")
		ui.Row(c).Gap(8).Children(func() {
			ui.TextInput(c, &s.model).Placeholder("模型名，例如 deepseek-chat").FontSize(kit.FSmall).Grow(1)
			if kit.Secondary(c, "列出模型").Disabled(s.busy).Clicked() {
				s.listModels(h)
			}
		})
		if len(s.models) > 0 {
			ui.Row(c).Gap(6).Wrap().Children(func() {
				for _, m := range s.models {
					if kit.Ghost(c, m).Key("m-" + m).Clicked() {
						s.model = m
					}
				}
			})
		}
		kit.Section(c, "API Key")
		has := s.d.Settings().HasKey && s.d.Settings().Provider == s.provider
		ph := "粘贴 Key"
		if has {
			ph = "已存过；要换就粘贴新的"
		}
		ui.TextInput(c, &s.key).Password().Placeholder(ph).FontSize(kit.FSmall).Label("API Key")
		if p.KeyURL != "" {
			kit.Note(c, "去哪拿 Key："+p.KeyURL)
		}
		kit.Note(c, "Key 只存在本机（model-keys.json，只有你能读），不进对话、不进日志。")
	}
	ui.Row(c).Gap(8).Children(func() {
		if kit.Primary(c, "保存").Key("save-model").Label("保存模型设置").Disabled(s.busy).Clicked() {
			s.saveModel()
		}
		if kit.Secondary(c, "测试").Disabled(s.busy).Clicked() {
			s.saveAndTest(h, false)
		}
	})
	s.noteLine(c)
}

// saveAndTest 存模型设置再测一次；advance 时（向导里点「下一步」）测通了自己进下一步，测不通留在这一步、写原因。
func (s *S) saveAndTest(h plugin.Host, advance bool) {
	s.saveModel()
	if s.noteBad {
		return // 没存上：原因已经写在下面
	}
	s.busy = true
	tested := s.formKey() // 测的是这一份；测试期间玩家改了表单，就不按旧结果自动往下走（CodeRabbit #105）
	h.Go(func() func() {
		ok, msg := s.d.Test(context.Background())
		return func() {
			same := s.formKey() == tested
			s.busy, s.modelOK, s.note, s.noteBad = false, ok && same, msg, !ok
			if ok && !same {
				s.note = "测试期间改了设置，再点一次「下一步」测新的"
			}
			if ok && same && advance && s.step == 3 {
				s.step++
			}
		}
	})
}

// formKey 是模型表单现在的样子（服务商、模型、地址、输入框里没保存的 Key），用来判断测试结果是不是这一份的。
// 保存时 Key 输入框会清空：测试期间又粘了 Key，这里就和开始测时不一样（CodeRabbit #105）。只在内存里比，不落盘。
func (s *S) formKey() string {
	return s.provider + "\x00" + strings.TrimSpace(s.model) + "\x00" + strings.TrimSpace(s.baseURL) + "\x00" + strings.TrimSpace(s.key)
}

func (s *S) saveModel() {
	st := s.d.Settings()
	st.Provider, st.Model, st.BaseURL = s.provider, strings.TrimSpace(s.model), strings.TrimSpace(s.baseURL)
	key := strings.TrimSpace(s.key)
	s.key = ""
	if err := s.d.Save(st, key); err != nil {
		s.note, s.noteBad = err.Error(), true
		return
	}
	s.formFor = ""
	s.note, s.noteBad = "已保存", false
	if s.d.OnModelChanged != nil {
		s.d.OnModelChanged()
	}
}

func (s *S) listModels(h plugin.Host) {
	s.busy = true
	prov, base, key := s.provider, s.baseURL, s.key
	h.Go(func() func() {
		ms, err := s.d.Models(context.Background(), prov, base, key)
		return func() {
			s.busy = false
			if err != nil {
				s.note, s.noteBad = err.Error(), true
				return
			}
			s.models = ms
		}
	})
}

func (s *S) setNote(r hiroapi.Result, okText string) {
	if r.OK {
		s.note, s.noteBad = okText, false
		return
	}
	s.note, s.noteBad = "出错了", true
	if r.Error != nil {
		s.note = r.Error.Message + "　" + r.Error.Hint
	}
}

func (s *S) noteLine(c *ui.Context) {
	if s.note == "" {
		return
	}
	col := kit.Good
	if s.noteBad {
		col = kit.Bad
	}
	ui.Text(c, s.note).FontSize(kit.FNote).TextColor(col).Wrap()
}

// ---- 账号密码 ----

var secrets = [][2]string{{"game-account", "游戏账号"}, {"game-password", "游戏密码"}}

func (s *S) secretsSection(c *ui.Context, h plugin.Host) {
	kit.Note(c, "游戏掉登录时，初星助手会把账号、密码依次复制到剪贴板，粘贴进登录框（45 秒后自动清空）。界面和对话里都看不到明文。")
	for _, sc := range secrets {
		name, label := sc[0], sc[1]
		v := s.secretVals[name]
		ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
			ui.Text(c, label).FontSize(kit.FSmall).TextColor(kit.Ink).Width(64)
			ph := "没存"
			if s.secretSet[name] {
				ph = "已存；要换就填新的"
			}
			ui.TextInput(c, &v).Password().Placeholder(ph).FontSize(kit.FSmall).Grow(1).Label(label)
			s.secretVals[name] = v
			if kit.Secondary(c, "保存").Key("secret-" + name).Label("保存" + label).Disabled(strings.TrimSpace(v) == "").Clicked() {
				val := v
				s.secretVals[name] = ""
				h.Go(func() func() {
					r := s.d.SetSecret(context.Background(), name, val)
					return func() {
						if r.OK {
							s.secretSet[name] = true
							s.secretNote = label + "已保存"
						} else if r.Error != nil {
							s.secretNote = r.Error.Message
						}
					}
				})
			}
		})
	}
	if s.secretNote != "" {
		kit.Note(c, s.secretNote)
	}
}

// ---- 设置面板 ----

func (s *S) panel(c *ui.Context, h plugin.Host, route string) {
	if route == "plugins" {
		if s.d.PluginsPanel != nil {
			s.d.PluginsPanel(c, h)
		} else {
			kit.Note(c, "还没装外部插件。")
		}
		return
	}
	ui.Text(c, "模型").FontSize(15).FontWeight(650).TextColor(kit.Ink)
	s.modelSection(c, h)
	ui.Text(c, "操作权限").FontSize(15).FontWeight(650).TextColor(kit.Ink).Padding(10, 0, 0, 0)
	s.permNote(c)
	ui.Text(c, "扫描").FontSize(15).FontWeight(650).TextColor(kit.Ink).Padding(10, 0, 0, 0)
	s.scanSection(c)
	ui.Text(c, "游戏连接").FontSize(15).FontWeight(650).TextColor(kit.Ink).Padding(10, 0, 0, 0)
	s.deviceLine(c, h)
	if kit.Secondary(c, "试连").Key("probe").Disabled(s.probing || h.LiveJob() != "").Clicked() {
		s.Probe(h)
	}
	ui.Text(c, "游戏账号密码（可选）").FontSize(15).FontWeight(650).TextColor(kit.Ink).Padding(10, 0, 0, 0)
	s.secretsSection(c, h)
	ui.Text(c, "运行环境").FontSize(15).FontWeight(650).TextColor(kit.Ink).Padding(10, 0, 0, 0)
	if s.boot == nil {
		s.boot = &BootStatus{}
		s.Recheck(h)
	}
	s.envSection(c, h)
	if s.boot.Ready && kit.Secondary(c, "重装运行环境").Key("reinstall").Disabled(s.booting).Clicked() {
		if err := s.d.BootReset(); err == nil {
			s.boot.Ready = false
			s.installEnv(h)
		}
	}
}

// needsKey：这个服务商要不要 Key（命令行类的 codex / claude 和「不用模型」不要）。
func needsKey(provider string) bool {
	switch provider {
	case "", "none", "codex", "claude":
		return false
	}
	return true
}
