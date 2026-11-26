package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/extplugin"
	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/settings"
)

const stationPluginID = "gkmsside"
const stationSchema = "gkms.hiro.station/v1"
const stationCheckInterval = time.Minute

// stationRetryDelays bounds the retries after an inconclusive check of an enabled
// station that is not mounted yet (offline at startup, a slow interpreter start).
// A verified result resets them; an explicit revocation or another owner does not retry.
var stationRetryDelays = []time.Duration{time.Minute, 5 * time.Minute, 15 * time.Minute}

type stationStatus struct {
	Schema        string `json:"schema"`
	State         string `json:"state"`
	IdentityValid bool   `json:"identity_valid"`
	Subject       string `json:"subject"`
}

func (s stationStatus) verified() bool {
	if s.Schema != stationSchema || !s.IdentityValid || s.State != "ready" && s.State != "tray_offline" {
		return false
	}
	hash, err := hex.DecodeString(s.Subject)
	return err == nil && len(hash) == 32 && strings.ToLower(s.Subject) == s.Subject
}

// runStation uses the installed station's fixed bridge, independently of the
// plugin manifest. The bridge owns device credentials and network validation.
func runStation(ctx context.Context, verb string) stationStatus {
	root, state := stationInstall()
	if state != "" {
		return stationStatus{State: state}
	}
	timeout := 10 * time.Second
	if verb == "pair" {
		timeout = 11 * time.Minute // The site's ten-minute confirmation window plus time for HTTP.
	}
	return runStationAt(ctx, root, verb, timeout)
}

func runStationAt(ctx context.Context, root, verb string, timeout time.Duration) stationStatus {
	python := filepath.Join(root, "python", "python.exe")
	bridge := filepath.Join(root, "app", "GakumasCLI", "station", "hiro_bridge.py")
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, "-I", "-B", bridge, verb)
	cmd.Dir = root
	cmd.WaitDelay = time.Second
	cmd.Env = []string{}
	for _, name := range []string{"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"} {
		if val, ok := os.LookupEnv(name); ok {
			cmd.Env = append(cmd.Env, name+"="+val)
		}
	}
	hideWindow(cmd)
	out, err := cmd.Output()
	var st stationStatus
	if err != nil || json.Unmarshal(out, &st) != nil || st.Schema != stationSchema {
		return stationStatus{State: "identity_unavailable"}
	}
	return st
}

// stationIntegration is updated only on the UI thread. Permit is evaluated
// when that thread loads, approves or refreshes a desktop plugin.
type stationIntegration struct {
	manager    *extplugin.Manager
	settings   *settingsCache
	install    func() (root, state string)
	probe      func(context.Context, string) stationStatus
	status     stationStatus
	active     string // The owner verified for this running session. Never restored from disk alone.
	cancel     context.CancelFunc
	wait       context.CancelFunc // A scheduled retry.
	delays     []time.Duration
	retries    int
	generation uint64
	discovered bool
	session    uint64
	present    bool
	next       time.Time
	note       string
}

func stationOn(pref Settings) bool { return pref.StationEnabled != nil && *pref.StationEnabled }

func setupStation(d appDeps, m *extplugin.Manager, cache *settingsCache, sd *settings.Deps) *stationIntegration {
	s := &stationIntegration{manager: m, settings: cache, install: d.StationInstall, probe: d.StationProbe,
		delays: stationRetryDelays}
	if s.install == nil {
		s.install = stationInstall
	}
	if s.probe == nil {
		s.probe = runStation
	}
	m.Permit = s.permit
	m.ShowInList = func(h plugin.Host, id string) bool {
		return id != stationPluginID || runtime.GOOS == "windows" && s.available(h)
	}
	if runtime.GOOS == "windows" {
		sd.StationPanel = s.panel
	}
	return s
}

func (s *stationIntegration) permit(id string) ([]string, bool) {
	if id != stationPluginID {
		return nil, true
	}
	if s.active == "" {
		return nil, false
	}
	pref := s.settings.Get()
	if !stationOn(pref) || pref.StationSubject != s.active {
		return nil, false
	}
	return []string{"GKMS_HIRO_SUBJECT=" + s.active}, true
}

// Discover locally once per opening, including reopening settings after an install or update.
func (s *stationIntegration) available(h plugin.Host) bool {
	var session uint64
	if panel, ok := h.(interface{ PanelSession() uint64 }); ok {
		session = panel.PanelSession()
	}
	if !s.discovered || s.session != session {
		_, state := s.install()
		s.discovered, s.session, s.present = true, session, state == ""
		if state == "not_installed" {
			s.forget()
		}
	}
	return s.present
}

func (s *stationIntegration) forget() {
	s.generation++
	if s.cancel != nil {
		s.cancel()
		s.cancel = nil
	}
	if s.wait != nil {
		s.wait()
		s.wait = nil
	}
	s.status, s.next, s.retries = stationStatus{}, time.Time{}, 0
	s.setActive("")
}

func (s *stationIntegration) setActive(subject string) {
	if s.active != subject {
		s.active = subject
		s.manager.Refresh(stationPluginID)
	}
}

func (s *stationIntegration) observe(status stationStatus) {
	s.status = status
	switch {
	case status.verified():
		pref := s.settings.Get()
		if stationOn(pref) && pref.StationSubject == status.Subject {
			s.setActive(status.Subject)
		} else {
			s.setActive("")
		}
	case status.Schema == stationSchema && status.State == "identity_invalid", status.State == "not_installed":
		s.setActive("")
	}
}

func (s *stationIntegration) restore(h plugin.Host) {
	if runtime.GOOS != "windows" {
		return
	}
	if _, approved, _ := s.manager.State(stationPluginID); approved && stationOn(s.settings.Get()) && s.available(h) {
		s.check(h, false)
	}
}

func (s *stationIntegration) check(h plugin.Host, pair bool) {
	if s.cancel != nil {
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	s.cancel = cancel
	generation := s.generation
	if pair {
		s.status = stationStatus{}
		s.setActive("")
	}
	h.Go(func() func() {
		defer cancel()
		if pair && ctx.Err() == nil {
			s.probe(ctx, "pair")
		}
		var status stationStatus
		if ctx.Err() == nil {
			status = s.probe(ctx, "status")
		}
		return func() {
			if generation != s.generation {
				return
			}
			s.cancel = nil
			s.next = h.Now().Add(stationCheckInterval)
			s.observe(status)
			s.retryLater(h, status)
		}
	})
}

// retryLater schedules the next bounded retry while an enabled, approved station
// is still unmounted after an inconclusive result. It never polls once mounted.
func (s *stationIntegration) retryLater(h plugin.Host, status stationStatus) {
	if status.verified() {
		s.retries = 0
		return
	}
	if s.active != "" || s.wait != nil || s.retries >= len(s.delays) || !stationOn(s.settings.Get()) ||
		status.Schema == stationSchema && status.State == "identity_invalid" || status.State == "not_installed" {
		return
	}
	if _, approved, _ := s.manager.State(stationPluginID); !approved {
		return
	}
	delay := s.delays[s.retries]
	s.retries++
	ctx, cancel := context.WithCancel(context.Background())
	s.wait = cancel
	generation := s.generation
	h.Go(func() func() {
		timer := time.NewTimer(delay)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return nil
		case <-timer.C:
		}
		return func() {
			if generation != s.generation || ctx.Err() != nil {
				return
			}
			s.wait = nil
			if s.active == "" {
				s.check(h, false)
			}
		}
	})
}

func (s *stationIntegration) setEnabled(enable bool) {
	pref := s.settings.Get()
	if enable {
		_, approved, _ := s.manager.State(stationPluginID)
		if !approved || !s.status.verified() {
			return
		}
		pref.StationSubject = s.status.Subject
	} else {
		pref.StationSubject = ""
	}
	pref.StationEnabled = &enable
	if err := s.settings.Save(pref, ""); err != nil {
		s.note = "无法保存采集站设置"
		return
	}
	s.note = ""
	if enable {
		s.setActive(s.status.Subject)
	} else {
		s.forget()
	}
}

func (s *stationIntegration) panel(c *ui.Context, h plugin.Host) {
	if c.WindowHidden() || !s.available(h) {
		return
	}
	kit.Section(c, "采集站")
	pref := s.settings.Get()
	installed, approved, enabled := s.manager.State(stationPluginID)
	if stationOn(pref) && approved && !h.Now().Before(s.next) {
		s.check(h, false)
	}
	message := "已检测到采集站。检查配对后可启用。"
	switch s.status.State {
	case "":
		if s.cancel != nil {
			message = "正在核对采集站…"
		}
	case "stale_install":
		message = "采集站安装记录已失效。"
	case "version_incompatible":
		message = "采集站接口版本不兼容。"
	case "identity_invalid":
		message = "采集站未配对或配对已失效。"
	case "identity_unavailable":
		message = "暂时无法验证采集站配对。"
	case "ready", "tray_offline":
		switch {
		case !s.status.verified():
			message = "采集站配对无法验证。"
		case stationOn(pref) && pref.StationSubject != s.status.Subject:
			message = "采集站配对已更换，请断开后重新启用。"
		case !installed:
			message = "请在「管理插件」安装采集站插件。"
		case !approved:
			message = "请在「管理插件」同意采集站插件的权限。"
		case enabled:
			message = "采集站已启用。"
		default:
			message = "采集站配对已验证，可以启用。"
		}
	}
	kit.Note(c, message)
	ui.Row(c).Gap(8).Children(func() {
		if kit.Secondary(c, "检查配对").Key("station-check").Disabled(s.cancel != nil).Clicked() {
			s.check(h, false)
		}
		if s.status.State == "identity_invalid" && kit.Secondary(c, "在浏览器配对").Key("station-pair").Disabled(s.cancel != nil).Clicked() {
			s.check(h, true)
		}
		if stationOn(pref) {
			if kit.Secondary(c, "断开采集站").Key("station-off").Clicked() {
				s.setEnabled(false)
			}
		} else if s.status.verified() && approved && kit.Primary(c, "启用采集站").Key("station-on").Disabled(s.cancel != nil || !h.Now().Before(s.next)).Clicked() {
			s.setEnabled(true)
		}
	})
	if s.note != "" {
		kit.Note(c, s.note)
	}
}
