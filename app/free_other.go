//go:build !windows

package main

import (
	"path/filepath"
	"syscall"
)

// freeBytes 是 dir 所在磁盘的剩余空间（dir 不存在时看最近的上级目录）。
func freeBytes(dir string) (uint64, error) {
	for {
		var st syscall.Statfs_t
		err := syscall.Statfs(dir, &st)
		if err == nil {
			return st.Bavail * uint64(st.Bsize), nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return 0, err
		}
		dir = parent
	}
}
