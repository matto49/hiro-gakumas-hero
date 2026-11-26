// Package plugin 是初星助手的插件接口（设计稿 §5）：内置插件和外部插件都包成 Plugin，宿主只认它。
package plugin

import (
	"context"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
)

// Card 是对话里的一张卡片（存进 threads/<id>.json，形状和旧版 src/lib/chat.ts 一致）。
type Card = map[string]any

// Host 是插件能碰的全部宿主能力。插件拿不到 Hiro 本身、Bootstrap、Key 存储。
type Host interface {
	// Call 调工具层命令（同步，别在界面线程的 view 里直接调，用 Go）。
	Call(ctx context.Context, args []string) hiroapi.Result
	// Go 在后台跑 work；work 返回的函数回到界面线程执行（可以是 nil）。
	Go(work func() func())
	// Ask 以用户身份往当前对话发一句话。
	Ask(text string)
	// Post 往当前对话加一张卡（以初星助手的身份）。
	Post(text string, card Card)
	// RunArgs 以用户身份跑一条工具层命令（label 是显示成用户说的话），结果做成卡片。
	RunArgs(label string, args []string)
	// OpenPanel 在右侧边面板打开某插件的某页。
	OpenPanel(pluginID, route string)
	// Job 是宿主盯着的某个任务（第一次问就开始盯；还没数据时 Job 为 nil）。
	Job(id string) *JobView
	// LiveJob 是现在活着的任务编号（没有为 ""）。
	LiveJob() string
	// ChatJob 是这局是不是在某个对话里开的（对话里有它的对局卡）；命令行等外部发起的为 false，只进「后台任务」。
	ChatJob(id string) bool
	// Busy 是助手正在回复（这时 Ask 以玩家身份说的话会被丢掉）。
	Busy() bool
	// ClosePanel 关掉侧边面板。
	ClosePanel()
	// Home 是 HIRO_HOME（读本机图片用）。
	Home() string
	// Now 是现在（测试可换）。
	Now() time.Time
}

// Command 是输入框里的 "/" 命令。Run 为空时打开插件面板的 Route。
type Command struct {
	Name  string
	Hint  string
	Route string
	Run   func(h Host)
}

// Chip 是输入框上面的快捷按钮：Say 是代用户说的话；Run 非空时直接执行（不经模型）。
type Chip struct {
	Label string
	Say   string
	Run   func(h Host)
}

// Starter 是新对话页上的一张引导卡：点了 Run（非空时）或代用户说 Say。
type Starter struct {
	Label string
	Sub   string
	Say   string
	Art   string  // 卡面小图（本机路径），可空
	Icon  *ui.SVG // 没有卡面时的图标，可空
	Attr  string  // 属性色点（Vo / Da / Vi），可空
	Run   func(h Host)
}

// NavItem 是侧栏插件列表里的一项（打开面板的 Route）。
type NavItem struct {
	Label string
	Icon  *ui.SVG
	Route string
}

// CardView 画一张卡。
type CardView func(c *ui.Context, h Host, card Card)

// Explained 是确认卡的解读：几行「键 值」和代价（「会操作游戏 · 用掉 1 次培育」）。
type Explained struct {
	Title    string      // 「开一局 初」
	Subtitle string      // 「月村手毬【Luna say maybe】· 普通」
	Attr     string      // 属性（Vo / Da / Vi），卡头的属性标签
	Art      string      // 卡头大图（本机路径），没有就不画
	Rows     [][2]string // 卡身几行「键 值」
	Thumbs   []string    // 卡身一排小图（支援卡），本机路径
	Groups   []ExplainGroup
	Cost     string // 卡脚左边的代价
}

// ExplainGroup 是确认卡卡身里的一组列表（「支援卡」6 张、「回忆」4 份）。
type ExplainGroup struct {
	Title string
	Items []ExplainItem
}

// ExplainItem 是列表里的一格：小图（可空）、名字、名字下的小字（等级等）、行尾的标签（租借 / 回忆时机），
// Tip 是鼠标停上去时的详细说明（效果文字等，可空）。
type ExplainItem struct {
	Thumb, Tag, Name, Note, Tip string
}

// Plugin 是一个插件给的全部东西（§5.1 的七类），哪项没有就留空。
type Plugin struct {
	ID   string
	Name string
	Icon *ui.SVG
	// Nav 是侧栏里的入口；空的话用 Name / Icon 打开面板首页（有 Panel 时）。
	Nav []NavItem
	// Tools 是认领的工具层命令名（ToolSpec.Name，如 "run start"）。
	Tools    []string
	Commands []Command
	Chips    []Chip
	Cards    map[string]CardView
	// Panel 画侧边面板的一页；route 是插件自己的子页（"" 为首页）。
	Panel    func(c *ui.Context, h Host, route string)
	Settings func(c *ui.Context, h Host)
	// Starters 是新对话页的引导卡（按最近几局等生成；view 里每帧调用，要快，慢的东西自己缓存）。
	Starters func(h Host) []Starter
	// Greeting 是新对话页问候语下面那行小字里的一段（「PLv 52 · AP 120/150」），没有返回 ""。
	Greeting func(h Host) string
	// Footer 画在侧栏底部（游戏连接、模型这类一直要看到的小状态）。
	Footer func(c *ui.Context, h Host)
	// Context 是给 agent 的「现在的情况」里的一段；ContextTitle 是小标题（空则用 Name）。
	Context      func(ctx context.Context, h Host) string
	ContextTitle string
	// Explain 解读一条待确认；认不出返回 ok=false。
	Explain func(h Host, p hiroapi.Pending) (Explained, bool)
	// OnJobEnd 在宿主看到某局从活着变成结束（不在 LiveStates）时调一次（界面线程；慢的事用 h.Go）。
	// h.Post 发到有这局对局卡的对话里。启动前就已结束的局不调。
	OnJobEnd func(h Host, id string, v *JobView)
}

// Navs 是插件在侧栏里的入口。
func (p *Plugin) Navs() []NavItem {
	if len(p.Nav) > 0 {
		return p.Nav
	}
	if p.Panel == nil {
		return nil
	}
	return []NavItem{{Label: p.Name, Icon: p.Icon}}
}

// 对话里错误卡上能点的动作（UserError.Actions）。
const (
	ActionSettings = "settings" // 打开设置（Key 不对、没配模型）
	ActionRetry    = "retry"    // 把刚才那句再发一次（断网、余额不足、服务商出错）
)

// UserError 是要给玩家看的出错：白话原因、下一步提示和能点的动作（GOAL §8：每条错误都要有白话原因和可点的下一步）。
// Err 是原始错误，只进日志，不显示。
type UserError struct {
	Message string
	Hint    string
	Actions []string
	Err     error
}

func (e *UserError) Error() string { return e.Message }
func (e *UserError) Unwrap() error { return e.Err }
