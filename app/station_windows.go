package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows/registry"
)

func stationInstall() (root, state string) {
	return stationInstallAt(`Software\GKMS\Station`)
}

func stationInstallAt(keyName string) (root, state string) {
	key, err := registry.OpenKey(registry.CURRENT_USER, keyName, registry.QUERY_VALUE|registry.WOW64_64KEY)
	if err != nil {
		if errors.Is(err, registry.ErrNotExist) {
			return "", "not_installed"
		}
		return "", "stale_install"
	}
	defer key.Close()
	root, rootType, rootErr := key.GetStringValue("InstallDir")
	data, dataType, dataErr := key.GetStringValue("DataDir")
	version, versionType, versionErr := key.GetStringValue("Version")
	if rootErr != nil || dataErr != nil || versionErr != nil || strings.TrimSpace(version) == "" ||
		rootType != registry.SZ || dataType != registry.SZ || versionType != registry.SZ ||
		!filepath.IsAbs(root) || len(filepath.VolumeName(root)) != 2 || !strings.EqualFold(filepath.Clean(data), filepath.Join(root, "data")) {
		return "", "stale_install"
	}
	iface, kind, err := key.GetIntegerValue("InterfaceVersion")
	if err != nil || iface != 1 || kind != registry.DWORD {
		return "", "version_incompatible"
	}
	var manifest struct{ Kind string }
	b, err := os.ReadFile(filepath.Join(root, "install.json"))
	if err != nil || json.Unmarshal(b, &manifest) != nil || manifest.Kind != "station" {
		return "", "stale_install"
	}
	if dir, err := os.Stat(data); err != nil || !dir.IsDir() {
		return "", "stale_install"
	}
	for _, path := range []string{filepath.Join(root, "python", "python.exe"), filepath.Join(root, "app", "GakumasCLI", "station", "hiro_bridge.py")} {
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() {
			return "", "stale_install"
		}
	}
	return root, ""
}
