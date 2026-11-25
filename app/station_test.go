package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStationSettingsPersistExplicitDisable(t *testing.T) {
	home := t.TempDir()
	a := &Assistant{Home: home, Keys: fileKeys{Path: filepath.Join(home, "keys.json")}}
	cache := &settingsCache{a: a}
	if err := cache.Save(Settings{Provider: "none", StationEnabled: true, StationSubject: strings.Repeat("a", 64)}, ""); err != nil {
		t.Fatal(err)
	}
	ui := settingsDeps(appDeps{Assistant: a}, cache)
	pref := ui.Settings()
	if !pref.StationEnabled {
		t.Fatal("the UI did not read the saved enabled preference")
	}
	pref.StationEnabled = false
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
	if restarted.StationEnabled || restarted.StationSubject != "" {
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
			if err := cache.Save(Settings{Provider: "none", StationEnabled: tt.enabled, StationSubject: tt.pin}, ""); err != nil {
				t.Fatal(err)
			}
			s := stationIntegration{settings: cache, status: tt.status}
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
