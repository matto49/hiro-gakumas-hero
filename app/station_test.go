package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/extplugin"
	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/shell"
)

func stationTestManifest(t *testing.T, home, id, name string) {
	t.Helper()
	dir := filepath.Join(home, "plugins", id)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(map[string]any{"id": id, "name": name, "command": []string{"unused-test-command"},
		"contributes": map[string]any{"tools": []map[string]string{{"name": "read"}}}})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "plugin.json"), b, 0o600); err != nil {
		t.Fatal(err)
	}
}

// A bridge reply is applied later on the UI thread. Hold the callback so the
// test can disconnect after the request has completed but before it is applied.
type stationTestHost struct {
	plugin.Host
	work func() func()
}

func (h *stationTestHost) Go(work func() func()) { h.work = work }
func (h *stationTestHost) Now() time.Time        { return time.Unix(100, 0) }

func TestStationRetainsOnlyAnAlreadyVerifiedOwner(t *testing.T) {
	home := t.TempDir()
	stationTestManifest(t, home, stationPluginID, "Station")
	cache := &settingsCache{a: &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "keys")}}}
	on, owner := true, strings.Repeat("a", 64)
	if err := cache.Save(Settings{Provider: "none", StationEnabled: &on, StationSubject: owner}, ""); err != nil {
		t.Fatal(err)
	}
	m := &extplugin.Manager{Home: home, Reg: &plugin.Registry{}}
	t.Cleanup(m.Close)
	s := &stationIntegration{manager: m, settings: cache, install: func() (string, string) { return home, "" }}
	m.Permit = s.permit
	m.Load()
	if err := m.Approve(stationPluginID); err != nil {
		t.Fatal(err)
	}
	ready := stationStatus{Schema: stationSchema, State: "ready", IdentityValid: true, Subject: owner}
	unknown := stationStatus{State: "identity_unavailable"}
	h := &stationTestHost{}
	probes := 0
	s.probe = func(ctx context.Context, verb string) stationStatus { probes++; return unknown }
	s.restore(h)
	if runtime.GOOS != "windows" {
		if h.work != nil {
			t.Fatal("restore scheduled a bridge on a non-Windows host")
		}
		s.check(h, false) // Exercise the state machine with the synthetic bridge.
	}
	h.work()()
	if probes != 1 || m.Reg.ByID(stationPluginID) != nil || len(m.AgentTools()) != 0 {
		t.Fatal("a transient cold-start result granted access or did not run exactly one restore")
	}
	s.observe(ready)
	mounted := m.Reg.ByID(stationPluginID)
	if mounted == nil || len(m.AgentTools()) != 1 {
		t.Fatal("verified restoration did not mount the approved tool")
	}
	for _, transient := range []stationStatus{unknown, {Schema: stationSchema, State: "identity_unavailable"}, {State: "stale_install"}} {
		s.observe(transient)
		m.Refresh(stationPluginID)
		if m.Reg.ByID(stationPluginID) != mounted || len(m.AgentTools()) != 1 {
			t.Fatal("transient result interrupted an already verified plugin")
		}
	}
	other := ready
	other.Subject = strings.Repeat("b", 64)
	for _, invalid := range []stationStatus{{Schema: stationSchema, State: "identity_invalid"}, other} {
		s.observe(invalid)
		s.observe(unknown)
		if m.Reg.ByID(stationPluginID) != nil || len(m.AgentTools()) != 0 {
			t.Fatal("an invalid or replaced pairing still has a tool")
		}
		s.observe(ready)
	}
	s.probe = func(context.Context, string) stationStatus { return ready }
	s.check(h, false)
	late := h.work()
	s.setEnabled(false)
	late()
	if m.Reg.ByID(stationPluginID) != nil || s.status.verified() || stationOn(cache.Get()) {
		t.Fatal("a late reply revived the disconnected station")
	}
}

func TestStationDesktopDiscoveryIsLocalAndOptIn(t *testing.T) {
	home := t.TempDir()
	stationTestManifest(t, home, stationPluginID, "Station fixture")
	stationTestManifest(t, home, "ordinary", "Ordinary fixture")
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "keys")}}
	if err := a.Save(Settings{Provider: "none", WizardDone: true}, ""); err != nil {
		t.Fatal(err)
	}
	run := &shell.SyncRunner{}
	present, discoveries, probes := false, 0, 0
	app := buildApp(appDeps{Home: home, Run: run, Assistant: a, Bootstrap: &Bootstrap{Home: home, Hiro: &Hiro{}},
		Ready: func(context.Context) bool { return true },
		Call: func(context.Context, []string) hiroapi.Result {
			return hiroapi.Result{OK: true, Data: json.RawMessage(`{}`)}
		},
		StationInstall: func() (string, string) {
			discoveries++
			if present {
				return home, ""
			}
			return "", "not_installed"
		},
		StationProbe: func(context.Context, string) stationStatus { probes++; return stationStatus{} },
	})
	t.Cleanup(plugins.Close)
	tt := ui.NewTester(app.View, 1280, 1000)
	draw := func() { run.Drain(); tt.Frame(); run.Drain(); tt.Frame() }
	draw()
	if discoveries != 0 || probes != 0 || !tt.HasText("新对话") || len(a.ExtTools()) != 0 {
		t.Fatal("startup touched station discovery/identity or changed the home screen")
	}
	app.OpenPanel("settings", "plugins")
	draw()
	if tt.HasText("Station fixture") || !tt.HasText("Ordinary fixture") {
		t.Fatal("the manager should show only the ordinary package")
	}
	app.OpenPanel("settings", "")
	draw()
	if tt.HasText("采集站") || tt.HasText("检查配对") || !tt.HasText("模型") {
		t.Fatal("ordinary settings exposed a missing integration or lost model settings")
	}
	opened := discoveries
	draw()
	if discoveries != opened || probes != 0 {
		t.Fatal("an idle frame rechecked the installation or launched a bridge")
	}
	present = true
	app.ClosePanel()
	app.OpenPanel("settings", "")
	draw()
	if tt.HasText("采集站") != (runtime.GOOS == "windows") || probes != 0 || len(a.ExtTools()) != 0 {
		t.Fatal("detecting an installation should only expose the opt-in section on Windows")
	}
	app.OpenPanel("settings", "plugins")
	draw()
	if tt.HasText("Station fixture") != (runtime.GOOS == "windows") || !tt.HasText("Ordinary fixture") || probes != 0 {
		t.Fatal("package visibility or disabled bridge activity was incorrect")
	}
	if runtime.GOOS != "windows" && discoveries != 0 {
		t.Fatalf("non-Windows host performed %d discovery checks", discoveries)
	}
}

func TestStationSettingsPersistExplicitDisable(t *testing.T) {
	home := t.TempDir()
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "keys.json")}}
	cache := &settingsCache{a: a}
	if err := cache.Save(Settings{Provider: "none"}, ""); err != nil {
		t.Fatal(err)
	}
	plain, err := os.ReadFile(a.settingsPath())
	if err != nil || strings.Contains(string(plain), "station_") {
		t.Fatalf("ordinary settings contained a station preference: %s, %v", plain, err)
	}
	enabled := true
	if err := cache.Save(Settings{Provider: "none", StationEnabled: &enabled, StationSubject: strings.Repeat("a", 64)}, ""); err != nil {
		t.Fatal(err)
	}
	ui := settingsDeps(appDeps{Assistant: a}, cache)
	pref := ui.Settings()
	if pref.StationEnabled == nil || !*pref.StationEnabled {
		t.Fatal("the UI did not read the saved enabled preference")
	}
	disabled := false
	pref.StationEnabled = &disabled
	pref.StationSubject = ""
	if err := ui.Save(pref, ""); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(a.settingsPath())
	if err != nil {
		t.Fatal(err)
	}
	var stored struct {
		Enabled *bool `json:"station_enabled"`
	}
	if err := json.Unmarshal(raw, &stored); err != nil {
		t.Fatal(err)
	}
	if stored.Enabled == nil || *stored.Enabled {
		t.Fatal("the disabled station preference was not persisted as false")
	}
	restarted := settingsDeps(appDeps{Assistant: a}, &settingsCache{a: a}).Settings()
	if restarted.StationEnabled == nil || *restarted.StationEnabled || restarted.StationSubject != "" {
		t.Fatal("restarting restored a disabled station's preference or binding")
	}
}

func TestStationPermitRequiresVerifiedPinnedSubject(t *testing.T) {
	subject := strings.Repeat("a", 64)
	other := strings.Repeat("b", 64)
	ready := stationStatus{Schema: stationSchema, State: "ready", IdentityValid: true, Subject: subject}
	for _, tt := range []struct {
		name    string
		status  stationStatus
		enabled bool
		pin     string
		allowed bool
	}{
		{"off", ready, false, subject, false},
		{"no probe at startup", stationStatus{}, true, subject, false},
		{"revoked", stationStatus{Schema: stationSchema, State: "identity_invalid"}, true, subject, false},
		{"compatible and paired", ready, true, subject, true},
		{"different paired account", ready, true, other, false},
		{"incompatible", stationStatus{Schema: stationSchema, State: "version_incompatible", IdentityValid: true, Subject: subject}, true, subject, false},
		{"unavailable", stationStatus{Schema: stationSchema, State: "identity_unavailable", IdentityValid: true, Subject: subject}, true, subject, false},
		{"paired when tray offline", stationStatus{Schema: stationSchema, State: "tray_offline", IdentityValid: true, Subject: subject}, true, subject, true},
		{"malformed fingerprint", stationStatus{Schema: stationSchema, State: "ready", IdentityValid: true, Subject: "x"}, true, "x", false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			home := t.TempDir()
			cache := &settingsCache{a: &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "keys.json")}}}
			if err := cache.Save(Settings{Provider: "none", StationEnabled: &tt.enabled, StationSubject: tt.pin}, ""); err != nil {
				t.Fatal(err)
			}
			s := stationIntegration{settings: cache, manager: &extplugin.Manager{Reg: &plugin.Registry{}}}
			s.observe(tt.status)
			env, allowed := s.permit(stationPluginID)
			if allowed != tt.allowed || allowed && (len(env) != 1 || env[0] != "GKMS_HIRO_SUBJECT="+subject) || !allowed && len(env) != 0 {
				t.Fatalf("allowed %v, environment %v", allowed, env)
			}
			if env, allowed = s.permit("another-plugin"); !allowed || len(env) != 0 {
				t.Fatalf("an unrelated plugin used the station gate: allowed %v, environment %v", allowed, env)
			}
		})
	}
}

// stationQueueHost runs background work in order, including work queued while draining.
type stationQueueHost struct {
	plugin.Host
	queue []func() func()
}

func (h *stationQueueHost) Go(work func() func()) { h.queue = append(h.queue, work) }
func (h *stationQueueHost) Now() time.Time        { return time.Unix(100, 0) }
func (h *stationQueueHost) drain() {
	for len(h.queue) > 0 {
		work := h.queue[0]
		h.queue = h.queue[1:]
		if apply := work(); apply != nil {
			apply()
		}
	}
}

func TestStationRetriesAnInconclusiveStartWithinBounds(t *testing.T) {
	home := t.TempDir()
	stationTestManifest(t, home, stationPluginID, "Station")
	cache := &settingsCache{a: &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "keys")}}}
	on, owner := true, strings.Repeat("a", 64)
	if err := cache.Save(Settings{Provider: "none", StationEnabled: &on, StationSubject: owner}, ""); err != nil {
		t.Fatal(err)
	}
	m := &extplugin.Manager{Home: home, Reg: &plugin.Registry{}}
	t.Cleanup(m.Close)
	s := &stationIntegration{manager: m, settings: cache, install: func() (string, string) { return home, "" },
		delays: []time.Duration{time.Millisecond, time.Millisecond, time.Millisecond}}
	m.Permit = s.permit
	m.Load()
	if err := m.Approve(stationPluginID); err != nil {
		t.Fatal(err)
	}
	ready := stationStatus{Schema: stationSchema, State: "ready", IdentityValid: true, Subject: owner}
	reply, probes := stationStatus{State: "identity_unavailable"}, 0
	s.probe = func(context.Context, string) stationStatus { probes++; return reply }
	h := &stationQueueHost{}

	s.check(h, false)
	h.drain()
	if probes != 1+len(s.delays) || m.Reg.ByID(stationPluginID) != nil || len(h.queue) != 0 || s.wait != nil {
		t.Fatalf("an inconclusive start ran %d checks; want one plus %d bounded retries and no access", probes, len(s.delays))
	}

	reply = ready
	s.check(h, false)
	h.drain()
	if m.Reg.ByID(stationPluginID) == nil || s.retries != 0 {
		t.Fatal("a verified check did not mount the station or reset the retries")
	}

	reply, probes = stationStatus{Schema: stationSchema, State: "identity_invalid"}, 0
	s.check(h, false)
	h.drain()
	if probes != 1 || m.Reg.ByID(stationPluginID) != nil || len(h.queue) != 0 {
		t.Fatal("an explicit revocation was retried or kept its tool")
	}

	reply, probes = stationStatus{State: "identity_unavailable"}, 0
	s.check(h, false)
	first := h.queue[0]
	h.queue = h.queue[1:]
	first()() // The check completes and schedules one retry.
	if s.wait == nil || len(h.queue) != 1 {
		t.Fatal("an inconclusive check of an unmounted station did not schedule a retry")
	}
	s.setEnabled(false)
	h.drain()
	if probes != 1 || s.wait != nil || stationOn(cache.Get()) {
		t.Fatal("disconnecting did not cancel the scheduled retry")
	}
}
