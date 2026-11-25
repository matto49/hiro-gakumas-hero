package extplugin

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func installed(t *testing.T) (*Manager, string, string) {
	t.Helper()
	t.Setenv("EXTPLUGIN_FAKE", "1")
	home := t.TempDir()
	src := fakeManifest(t).Dir
	m := &Manager{Home: home, Reg: &plugin.Registry{}}
	m.Load()
	if _, err := m.Install(src); err != nil {
		t.Fatal(err)
	}
	if err := m.Approve("demo"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(m.Close)
	return m, home, src
}

// CR#0：清单没了 / 不再被同意覆盖时，重新读要把它停掉、注销。
func TestReloadUnregistersIneligible(t *testing.T) {
	m, home, _ := installed(t)
	if err := os.RemoveAll(filepath.Join(home, "plugins", "demo")); err != nil {
		t.Fatal(err)
	}
	m.Load()
	if m.Reg.ByID("demo") != nil || m.Dir("demo") != "" || len(m.AgentTools()) != 0 {
		t.Error("清单没了还在注册表里")
	}
}

// CR#1：重新读时没变的插件不重启。
func TestReloadKeepsUnchangedInstance(t *testing.T) {
	m, _, _ := installed(t)
	before := m.exts["demo"]
	m.Load()
	if m.exts["demo"] != before {
		t.Error("没变的插件被重建了")
	}
}

// CR#2：从已安装的目录本身（或它里面）安装要拒，不能先把自己删了。
func TestInstallFromInstalledDirRejected(t *testing.T) {
	m, home, _ := installed(t)
	dst := filepath.Join(home, "plugins", "demo")
	if _, err := m.Install(dst); err == nil {
		t.Fatal("从安装目录本身安装应报错")
	}
	if _, err := os.Stat(filepath.Join(dst, "plugin.json")); err != nil {
		t.Error("原来装的插件被删了")
	}
}

// CR#3：同一个 id 换了新包要重新同意，不沿用旧的同意。
func TestReinstallNeedsApprovalAgain(t *testing.T) {
	m, _, src := installed(t)
	if _, err := m.Install(src); err != nil {
		t.Fatal(err)
	}
	if m.Reg.ByID("demo") != nil {
		t.Error("重装后没同意就启用了")
	}
}

// CR#4：源目录是符号链接时照样装得上（或明确报错），不能返回 nil 清单。
func TestInstallFromSymlink(t *testing.T) {
	t.Setenv("EXTPLUGIN_FAKE", "1")
	home := t.TempDir()
	src := fakeManifest(t).Dir
	link := filepath.Join(t.TempDir(), "link")
	if err := os.Symlink(src, link); err != nil {
		t.Skip(err)
	}
	m := &Manager{Home: home, Reg: &plugin.Registry{}}
	m.Load()
	man, err := m.Install(link)
	if err == nil && man == nil {
		t.Fatal("装成功却没有清单")
	}
}

// CR#5：卸载失败时插件还在列表里，能再点卸载。
func TestFailedUninstallStaysVisible(t *testing.T) {
	m, home, _ := installed(t)
	plugins := filepath.Join(home, "plugins")
	_ = os.Chmod(plugins, 0o500) // 删不了目录
	defer os.Chmod(plugins, 0o755)
	if err := m.Remove("demo"); err == nil {
		t.Skip("这个系统上 chmod 挡不住删除")
	}
	if m.installed["demo"] == nil {
		t.Error("卸载失败后插件不该从列表里消失")
	}
}

// CR#6：两个插件生成同名工具时都不给 agent，免得模型选错。
func TestCollidingToolNamesDropped(t *testing.T) {
	tools := uniqueTools([]AgentTool{{Name: "plugin_foo_bar_baz", Plugin: "foo-bar"}, {Name: "plugin_foo_bar_baz", Plugin: "foo"}, {Name: "plugin_x_y", Plugin: "x"}})
	if len(tools) != 1 || tools[0].Name != "plugin_x_y" {
		t.Errorf("撞名的工具要都去掉：%+v", tools)
	}
}

// CR#8/9：插件不读 stdin 时，请求在超时内返回，等待者也被清掉。
func TestCallTimesOutWhenPluginStopsReading(t *testing.T) {
	old := CallTimeout
	CallTimeout = 500 * time.Millisecond
	defer func() { CallTimeout = old }()
	c := newClient(t.TempDir(), []string{"sleep", "30"}, os.Environ(), nil)
	defer c.Close()
	big := strings.Repeat("x", 1<<20) // 比管道缓冲大，写会卡住
	done := make(chan error, 1)
	go func() { done <- c.Call(context.Background(), "tool.call", map[string]string{"big": big}, nil) }()
	select {
	case err := <-done:
		if err == nil {
			t.Error("应该超时")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("写管道卡住，没在超时内返回")
	}
	c.mu.Lock()
	n := len(c.waiting)
	c.mu.Unlock()
	if n != 0 {
		t.Errorf("还剩 %d 个等待者", n)
	}
}

// CR2#1：源目录把 plugins 目录包在里面时也要拒（否则复制会套娃）。
func TestInstallFromAncestorOfPluginsRejected(t *testing.T) {
	t.Setenv("EXTPLUGIN_FAKE", "1")
	root := t.TempDir()
	home := filepath.Join(root, "home")
	src := fakeManifest(t).Dir
	// 把清单放到 root（它包含 home/plugins）
	b, _ := os.ReadFile(filepath.Join(src, "plugin.json"))
	_ = os.WriteFile(filepath.Join(root, "plugin.json"), b, 0o644)
	m := &Manager{Home: home, Reg: &plugin.Registry{}}
	_, err := m.Install(root)
	if err == nil || !strings.Contains(err.Error(), "不能包含初星助手的数据目录") {
		t.Fatalf("要在复制前按「包含数据目录」拒掉，得到 %v", err)
	}
	if _, err := os.Stat(filepath.Join(home, "plugins", ".demo.installing")); !os.IsNotExist(err) {
		t.Error("拒掉之前不该建临时目录")
	}
}

// CR2#2：插件进程退出但留下占着 stdout 的子进程时，也要很快认出它退出了。
func TestExitDetectedWhileChildHoldsStdout(t *testing.T) {
	c := newClient(t.TempDir(), []string{"sh", "-c", "sleep 8 2>/dev/null & exit 0"}, os.Environ(), nil)
	defer c.Close()
	c.mu.Lock()
	if err := c.startLocked(); err != nil {
		c.mu.Unlock()
		t.Fatal(err)
	}
	c.mu.Unlock()
	deadline := time.Now().Add(DrainGrace + 3*time.Second)
	for time.Now().Before(deadline) {
		c.mu.Lock()
		gone := c.cmd == nil
		c.mu.Unlock()
		if gone {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Error("父进程退出了，客户端还当它在跑")
}

// CR2#3：写超时只杀它当时写的那一代进程，不误杀新起的。
func TestTimeoutKillsOnlyItsGeneration(t *testing.T) {
	c := newClient(t.TempDir(), []string{"sleep", "30"}, os.Environ(), nil)
	defer c.Close()
	c.mu.Lock()
	_ = c.startLocked()
	gen := c.gen
	c.gen++ // 假装中间换了一代
	newer := c.cmd
	c.killGenLocked(gen)
	still := c.cmd == newer && c.cmd != nil
	c.mu.Unlock()
	if !still {
		t.Error("杀掉了别的一代")
	}
}
