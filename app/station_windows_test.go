package main

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"golang.org/x/sys/windows/registry"
)

func TestWindowsStationInstallDiscovery(t *testing.T) {
	root := t.TempDir()
	keyName := `Software\GKMS-HiroTest-` + strconv.Itoa(os.Getpid()) + "-" + filepath.Base(filepath.Dir(root))
	assertDiscovery := func(wantRoot, wantState string) {
		t.Helper()
		gotRoot, gotState := stationInstallAt(keyName)
		if gotRoot != wantRoot || gotState != wantState {
			t.Fatalf("discovery = (%q, %q), want (%q, %q)", gotRoot, gotState, wantRoot, wantState)
		}
	}
	assertDiscovery("", "not_installed")

	key, existing, err := registry.CreateKey(registry.CURRENT_USER, keyName, registry.SET_VALUE|registry.WOW64_64KEY)
	if err != nil {
		t.Fatal(err)
	}
	if existing {
		key.Close()
		t.Fatal("temporary test key already exists")
	}
	t.Cleanup(func() {
		if err := key.Close(); err != nil {
			t.Error(err)
		}
		if err := registry.DeleteKey(registry.CURRENT_USER, keyName); err != nil {
			t.Error(err)
		}
	})
	for name, value := range map[string]string{
		"InstallDir": root,
		"DataDir":    filepath.Join(root, "data"),
		"Version":    "4.6.6",
	} {
		if err := key.SetStringValue(name, value); err != nil {
			t.Fatal(err)
		}
	}
	if err := key.SetDWordValue("InterfaceVersion", 1); err != nil {
		t.Fatal(err)
	}
	assertDiscovery("", "stale_install")

	for _, name := range []string{
		"data",
		"python",
		filepath.Join("app", "GakumasCLI", "station"),
	} {
		if err := os.MkdirAll(filepath.Join(root, name), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	files := map[string]string{
		"install.json":                        `{"kind":"node","version":"4.6.6"}`,
		filepath.Join("python", "python.exe"): "",
		filepath.Join("app", "GakumasCLI", "station", "hiro_bridge.py"): "",
	}
	for name, contents := range files {
		if err := os.WriteFile(filepath.Join(root, name), []byte(contents), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	assertDiscovery("", "stale_install")
	if err := os.WriteFile(filepath.Join(root, "install.json"), []byte(`{"kind":"station","version":"4.6.6"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	assertDiscovery(root, "")
}

func TestWindowsStationBridgeProcess(t *testing.T) {
	runtime := os.Getenv("HIRO_TEST_STATION_PYTHON_DIR")
	if runtime == "" {
		t.Skip("set HIRO_TEST_STATION_PYTHON_DIR to an isolated portable Python runtime directory")
	}
	root := t.TempDir()
	if err := os.CopyFS(filepath.Join(root, "python"), os.DirFS(runtime)); err != nil {
		t.Fatal(err)
	}
	bridge := filepath.Join(root, "app", "GakumasCLI", "station", "hiro_bridge.py")
	if err := os.MkdirAll(filepath.Dir(bridge), 0o700); err != nil {
		t.Fatal(err)
	}
	const script = `import json, sys, time
if sys.argv[1] == "pair":
    with open("probe-started", "w") as marker:
        marker.write("started")
    time.sleep(10)
print(json.dumps({"schema": "gkms.hiro.station/v1", "state": "ready", "identity_valid": True, "subject": "a" * 64}))
`
	if err := os.WriteFile(bridge, []byte(script), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := runStationAt(context.Background(), root, "status", 10*time.Second); !got.verified() || got.Subject != strings.Repeat("a", 64) {
		t.Fatalf("status did not verify the fixture subject: %+v", got)
	}
	if got := runStationAt(context.Background(), root, "pair", 2*time.Second); got.State != "identity_unavailable" || got.verified() {
		t.Fatalf("deadline did not produce a transient verification failure: %+v", got)
	}
	if _, err := os.Stat(filepath.Join(root, "probe-started")); err != nil {
		t.Fatalf("the slow Python process did not start: %v", err)
	}
}
