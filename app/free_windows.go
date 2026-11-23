//go:build windows

package main

import (
	"path/filepath"
	"syscall"
	"unsafe"
)

var getDiskFreeSpaceEx = syscall.NewLazyDLL("kernel32.dll").NewProc("GetDiskFreeSpaceExW")

// freeBytes 是 dir 所在磁盘的剩余空间（dir 不存在时看最近的上级目录）。
func freeBytes(dir string) (uint64, error) {
	for {
		p, err := syscall.UTF16PtrFromString(dir)
		if err != nil {
			return 0, err
		}
		var avail uint64
		r, _, callErr := getDiskFreeSpaceEx.Call(uintptr(unsafe.Pointer(p)), uintptr(unsafe.Pointer(&avail)), 0, 0)
		if r != 0 {
			return avail, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return 0, callErr
		}
		dir = parent
	}
}
