package main

import (
	"net/http"
	neturl "net/url"
	"path"
	"path/filepath"
	"runtime"
	"strings"
	"sync"

	"github.com/egoist/mygo"
	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/extplugin"
	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/cockpit"
)

// 侧边面板里的网页视图（设计稿 §5.6）：复盘页和外部插件的网页面板。只加载本机文件，不给任何绑定，
// 一律按不可信页面开；外部插件的页面加 CSP，不能联网。

// PluginCSP 是外部插件网页的内容安全策略：只读自己目录里的东西，不能连网。
const PluginCSP = "default-src 'self' 'unsafe-inline' data:; connect-src 'none'"

// webURL 是自定义协议下某个相对路径的地址（Windows 的 WebView2 用 http://<协议>.localhost/）。
func webURL(scheme, rel, goos string) string {
	rel = strings.TrimLeft(rel, "/")
	if goos == "windows" {
		return "http://" + scheme + ".localhost/" + rel
	}
	return scheme + "://localhost/" + rel
}

// pluginFiles 是 hiroplug:// 的处理：/<插件 id>/<文件>，只给插件目录里的文件，带 CSP。
func pluginFiles(dirOf func(id string) string) http.Handler { return pluginFilesFor(dirOf, nil) }

// pluginFilesFor 同 pluginFiles，另外只给发请求的那个插件页面它自己目录里的文件（ownerOf 认出是哪个插件的页面；
// 认不出的一律不给）。各插件页面在 Windows 上同源，不这样插件 A 能读到插件 B 的文件。
func pluginFilesFor(dirOf func(id string) string, ownerOf func(r *http.Request) string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p := r.URL.Path
		if p != path.Clean(p) || strings.Contains(p, "..") {
			http.NotFound(w, r)
			return
		}
		id, rel, ok := strings.Cut(strings.TrimPrefix(p, "/"), "/")
		if ownerOf != nil && ownerOf(r) != id {
			http.NotFound(w, r)
			return
		}
		dir := dirOf(id)
		if !ok || dir == "" || rel == "" {
			http.NotFound(w, r)
			return
		}
		file := filepath.Join(dir, filepath.FromSlash(rel))
		if r2, err := filepath.Rel(dir, file); err != nil || strings.HasPrefix(r2, "..") || !exists(file) {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Security-Policy", PluginCSP)
		http.ServeFile(w, r, file)
	})
}

// webPanels 按地址缓存网页视图（每个地址一个，换页不重载）。
type webPanels struct {
	mu     sync.Mutex
	win    *mygo.Window
	views  map[string]*mygo.WebView
	owners map[*mygo.Page]string // 插件网页 → 插件 id
}

// ownerOf 是发请求的页面属于哪个插件（hiroplug 协议用）。
func (wp *webPanels) ownerOf(r *http.Request) string {
	p := mygo.CallerPage(r.Context())
	wp.mu.Lock()
	defer wp.mu.Unlock()
	return wp.owners[p]
}

func (wp *webPanels) setWindow(w *mygo.Window) {
	wp.mu.Lock()
	wp.win = w
	wp.mu.Unlock()
}

// show 在面板里画一个本机页面；页面只能留在自己的源上，跳别处一律拦下。
func (wp *webPanels) show(c *ui.Context, url string) { wp.showFor(c, url, "") }

// showFor 同 show，owner 是插件 id（插件网页面板）：记下这个页面属于哪个插件。
func (wp *webPanels) showFor(c *ui.Context, url, owner string) {
	if url == "" {
		kit.Note(c, "没有这个页面")
		return
	}
	wp.mu.Lock()
	win := wp.win
	if wp.views == nil {
		wp.views = map[string]*mygo.WebView{}
	}
	v := wp.views[url]
	wp.mu.Unlock()
	if win == nil {
		kit.Note(c, "网页视图还没准备好")
		return
	}
	if v == nil {
		nv, err := win.NewWebView(mygo.WebViewOptions{URL: url, Page: mygo.PageOptions{Untrusted: true}, BackgroundColor: "#FFFBF6"})
		if err != nil {
			ui.Text(c, "网页打不开："+err.Error()).FontSize(kit.FNote).TextColor(kit.Bad)
			return
		}
		origin := originOf(url)
		nv.Page().OnWillNavigate(func(e *mygo.NavigateEvent) {
			if !strings.HasPrefix(e.URL, origin) {
				e.PreventDefault()
			}
		})
		wp.mu.Lock()
		wp.views[url] = nv
		if owner != "" {
			if wp.owners == nil {
				wp.owners = map[*mygo.Page]string{}
			}
			wp.owners[nv.Page()] = owner
		}
		wp.mu.Unlock()
		v = nv
	}
	ui.WebView(c, v).Grow(1).MinHeight(480).Radius(kit.R12)
}

// originOf 是地址的「协议://主机」部分（导航只许留在这里）。
func originOf(raw string) string {
	u, err := neturl.Parse(raw)
	if err != nil {
		return raw
	}
	return u.Scheme + "://" + u.Host + "/"
}

// reviewURL 是某局复盘页的地址（hiro run review 生成在 jobs/<任务>/review.html，由 homeFiles 给出）。
func reviewURL(jobID string) string { return webURL("hiro", "review/"+jobID+".html", runtime.GOOS) }

// pluginWebURL 是插件网页面板文件的地址。
func pluginWebURL(dir, file string) string {
	rel, err := filepath.Rel(dir, file)
	if err != nil {
		return ""
	}
	return webURL("hiroplug", filepath.Base(dir)+"/"+filepath.ToSlash(rel), runtime.GOOS)
}

// hook 把网页视图接到游戏运行现状（复盘页）和外部插件（网页面板）上。
func (wp *webPanels) hook(dirOf func(id string) string) {
	cockpit.ReviewView = func(c *ui.Context, _ plugin.Host, jobID string) { wp.show(c, reviewURL(jobID)) }
	extplugin.WebPanel = func(c *ui.Context, _ plugin.Host, file string, _ bool) {
		url, owner := pluginFileURL(dirOf, file)
		wp.showFor(c, url, owner)
	}
}

// pluginFileURL 找出文件属于哪个插件目录，给出 hiroplug 地址和插件 id。
func pluginFileURL(dirOf func(id string) string, file string) (string, string) {
	for dir := filepath.Dir(file); dir != filepath.Dir(dir); dir = filepath.Dir(dir) {
		if id := filepath.Base(dir); dirOf(id) == dir {
			return pluginWebURL(dir, file), id
		}
	}
	return "", ""
}

func runtimeGOOS() string { return runtime.GOOS }
