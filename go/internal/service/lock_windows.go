//go:build windows

package service

import (
	"errors"
	"os"

	"golang.org/x/sys/windows"
)

// 锁文件开头 1 字节（Python msvcrt.locking(fd, LK_NBLCK, 1) 锁的也是这一段）。
func tryLock(f *os.File) error {
	ol := new(windows.Overlapped)
	err := windows.LockFileEx(windows.Handle(f.Fd()), windows.LOCKFILE_EXCLUSIVE_LOCK|windows.LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, ol)
	if errors.Is(err, windows.ERROR_LOCK_VIOLATION) || errors.Is(err, windows.ERROR_IO_PENDING) {
		return errLocked
	}
	return err
}

func unlock(f *os.File) error {
	return windows.UnlockFileEx(windows.Handle(f.Fd()), 0, 1, 0, new(windows.Overlapped))
}
