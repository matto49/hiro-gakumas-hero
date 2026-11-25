//go:build !windows

package extplugin

import "os/exec"

func hide(*exec.Cmd) {}
