package main

import (
	"errors"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows/registry"
)

func stationInstall() (root, state string) {
	key, err := registry.OpenKey(registry.CURRENT_USER, `Software\GKMS\Station`, registry.QUERY_VALUE|registry.WOW64_64KEY)
	if err != nil {
		if errors.Is(err, registry.ErrNotExist) {
			return "", "not_installed"
		}
		return "", "stale_install"
	}
	defer key.Close()
	root, _, rootErr := key.GetStringValue("InstallDir")
	data, _, dataErr := key.GetStringValue("DataDir")
	version, _, versionErr := key.GetStringValue("Version")
	if rootErr != nil || dataErr != nil || versionErr != nil || strings.TrimSpace(version) == "" ||
		!filepath.IsAbs(root) || !strings.EqualFold(filepath.Clean(data), filepath.Join(root, "data")) {
		return "", "stale_install"
	}
	iface, kind, err := key.GetIntegerValue("InterfaceVersion")
	if err != nil || iface != 1 || kind != registry.DWORD {
		return "", "version_incompatible"
	}
	return root, ""
}
