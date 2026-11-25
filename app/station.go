package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/extplugin"
	"github.com/matto49/hiro-gakumas-hero/app/kit"
	"github.com/matto49/hiro-gakumas-hero/app/plugin"
)

const stationPluginID = "gkmsside"
const stationSchema = "gkms.hiro.station/v1"
const stationCheckInterval = 5 * time.Second

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
func runStation(verb string) stationStatus {
	root, state := stationInstall()
	if state != "" {
		return stationStatus{State: state}
	}
	python := filepath.Join(root, "python", "python.exe")
	bridge := filepath.Join(root, "app", "GakumasCLI", "station", "hiro_bridge.py")
	for _, path := range []string{filepath.Join(root, "install.json"), python, bridge} {
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() {
			return stationStatus{State: "stale_install"}
		}
	}
	timeout := 3 * time.Second
	if verb == "pair" {
		timeout = 11 * time.Minute // The site's ten-minute confirmation window plus time for HTTP.
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
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
	manager  *extplugin.Manager
	settings *settingsCache
	probe    func(verb string) stationStatus
	status   stationStatus
	busy     bool
	next     time.Time
	note     string
}

func (s *stationIntegration) permit(id string) ([]string, bool) {
	if id != stationPluginID {
		return nil, true
	}
	pref := s.settings.Get()
	if !pref.StationEnabled || !s.status.verified() || pref.StationSubject != s.status.Subject {
		return nil, false
	}
	return []string{"GKMS_HIRO_SUBJECT=" + s.status.Subject}, true
}

func (s *stationIntegration) tick(h plugin.Host) {
	if s.settings.Get().StationEnabled && !h.Now().Before(s.next) {
		s.check(h, false)
	}
}

func (s *stationIntegration) check(h plugin.Host, pair bool) {
	if s.busy {
		return
	}
	s.busy = true
	if pair {
		// Pause an old account immediately while browser pairing is in progress.
		s.status = stationStatus{State: "identity_unavailable"}
		s.manager.Refresh(stationPluginID)
	}
	h.Go(func() func() {
		if pair {
			s.probe("pair")
		}
		status := s.probe("status")
		return func() {
			s.busy = false
			s.status = status
			s.next = h.Now().Add(stationCheckInterval)
			s.manager.Refresh(stationPluginID)
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
	pref.StationEnabled = enable
	if err := s.settings.Save(pref, ""); err != nil {
		s.note = "无法保存采集站设置"
		return
	}
	s.note = ""
	s.manager.Refresh(stationPluginID)
}

func (s *stationIntegration) panel(c *ui.Context, h plugin.Host) {
	kit.Section(c, "采集站")
	if !h.Now().Before(s.next) {
		s.check(h, false)
	}
	installed, approved, enabled := s.manager.State(stationPluginID)
	pref := s.settings.Get()
	message := "正在核对采集站…"
	switch s.status.State {
	case "not_installed":
		message = "当前 Windows 用户未登记采集站。已安装时，请启动或更新托盘。"
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
		case pref.StationEnabled && pref.StationSubject != s.status.Subject:
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
		if kit.Secondary(c, "重新检查").Key("station-check").Disabled(s.busy).Clicked() {
			s.check(h, false)
		}
		if s.status.State == "identity_invalid" && kit.Secondary(c, "在浏览器配对").Key("station-pair").Disabled(s.busy).Clicked() {
			s.check(h, true)
		}
		if pref.StationEnabled {
			if kit.Secondary(c, "断开采集站").Key("station-off").Clicked() {
				s.setEnabled(false)
			}
		} else if s.status.verified() && approved && kit.Primary(c, "启用采集站").Key("station-on").Disabled(s.busy).Clicked() {
			s.setEnabled(true)
		}
	})
	if s.note != "" {
		kit.Note(c, s.note)
	}
}
