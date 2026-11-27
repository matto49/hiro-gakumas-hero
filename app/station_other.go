//go:build !windows

package main

func stationInstall() (root, state string) { return "", "not_installed" }
