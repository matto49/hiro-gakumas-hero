package extplugin

import (
	"context"
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

func TestPermitAppliesToApprovalReloadAndRefresh(t *testing.T) {
	t.Setenv("EXTPLUGIN_FAKE", "1")
	allowed := false
	env := []string{"TEST_STATION_SUBJECT=first"}
	permit := func(id string) ([]string, bool) { return env, allowed }
	home := t.TempDir()
	m := &Manager{Home: home, Reg: &plugin.Registry{}, Permit: permit}
	t.Cleanup(m.Close)
	if _, err := m.Install(fakeManifest(t).Dir); err != nil {
		t.Fatal(err)
	}
	if err := m.Approve("demo"); err != nil {
		t.Fatal(err)
	}
	if present, approved, on := m.State("demo"); !present || !approved || on {
		t.Fatalf("gated approval: present %v approved %v on %v", present, approved, on)
	}
	allowed = true
	m.Refresh("demo")
	if m.Reg.ByID("demo") == nil || !slices.Contains(m.exts["demo"].c.env, env[0]) {
		t.Fatal("refresh did not register the approved plugin with the permitted environment")
	}
	tools := m.AgentTools()
	if len(tools) != 1 || !tools[0].Run(context.Background(), newHost(), map[string]any{"text": "test"}).OK {
		t.Fatal("the approved tool did not run")
	}
	allowed = false
	m.Refresh("demo")
	if m.Reg.ByID("demo") != nil || m.Dir("demo") != "" || len(m.AgentTools()) != 0 {
		t.Fatal("refresh did not unregister the plugin")
	}
	if got := tools[0].Run(context.Background(), newHost(), nil); got.OK || got.Error == nil {
		t.Fatalf("a previously captured tool remained executable: %+v", got)
	}
	if _, err := os.Stat(filepath.Join(home, "plugins", "demo", "plugin.json")); err != nil {
		t.Fatalf("disabling discarded the plugin files: %v", err)
	}
	restarted := &Manager{Home: home, Reg: &plugin.Registry{}, Permit: permit}
	t.Cleanup(restarted.Close)
	restarted.Load()
	if present, approved, on := restarted.State("demo"); !present || !approved || on {
		t.Fatalf("gated startup: present %v approved %v on %v", present, approved, on)
	}
	allowed = true
	restarted.Load()
	if restarted.Reg.ByID("demo") == nil {
		t.Fatal("startup did not honor the restored permit")
	}
	if err := restarted.Remove("demo"); err != nil || restarted.Reg.ByID("demo") != nil {
		t.Fatalf("uninstall: %v", err)
	}
}

func TestNativePanelRefreshLimitAndVisibility(t *testing.T) {
	now := time.Unix(100, 0)
	interval := 1
	s := screen{session: 7}
	s.apply(rendered{Tree: &Node{Type: "text"}, RefreshMS: &interval}, now, true)
	if s.refresh != time.Second || s.needsRender(now.Add(time.Millisecond), true, 7) {
		t.Fatal("refresh interval was not bounded by one second")
	}
	if !s.needsRender(now.Add(time.Millisecond), true, 8) {
		t.Fatal("opening a new panel session did not refresh it")
	}
	if s.needsRender(now.Add(time.Second), false, 8) || !s.needsRender(now.Add(time.Second), true, 7) {
		t.Fatal("only a visible panel should refresh at the deadline")
	}
	s.v.busy = "button"
	if s.needsRender(now.Add(time.Second), true, 8) {
		t.Fatal("refresh overlapped an event")
	}
	s.v.busy = ""
	interval = 1_000_000
	s.apply(rendered{RefreshMS: &interval}, now, true)
	if s.refresh != 30*time.Second {
		t.Fatal("refresh interval was not bounded by thirty seconds")
	}
	interval = 0
	s.apply(rendered{RefreshMS: &interval}, now, true)
	if s.needsRender(now.Add(time.Minute), true, 8) {
		t.Fatal("refreshing still enabled after opt-out")
	}
	interval = 2_000
	s.apply(rendered{RefreshMS: &interval}, now, false)
	if s.refresh != 0 {
		t.Fatal("refreshMs on a card or on settings was not ignored")
	}
}
