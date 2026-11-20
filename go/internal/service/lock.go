package service

import (
	"errors"
	"os"
	"path/filepath"
	"time"
)

// FileLock 是跨进程文件锁：一台机器上只跑一个 hiro 服务、只有一个客户端在拉起服务。
// 和 Python 版 hiro.cli.lock 认同一把锁（Unix flock；Windows 锁文件开头 1 字节）。
type FileLock struct {
	path string
	f    *os.File
}

// NewFileLock 建一把锁（还没拿）。
func NewFileLock(path string) *FileLock { return &FileLock{path: path} }

// Acquire 拿锁；blocking=false 时拿不到立刻返回 false。
func (l *FileLock) Acquire(blocking bool, timeout time.Duration) (bool, error) {
	if err := os.MkdirAll(filepath.Dir(l.path), 0o750); err != nil {
		return false, err
	}
	f, err := os.OpenFile(l.path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return false, err
	}
	deadline := time.Now().Add(timeout)
	for {
		err := tryLock(f)
		if err == nil {
			l.f = f
			return true, nil
		}
		if !errors.Is(err, errLocked) {
			_ = f.Close()
			return false, err
		}
		if !blocking || time.Now().After(deadline) {
			_ = f.Close()
			return false, nil
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// Release 放锁。
func (l *FileLock) Release() {
	if l.f == nil {
		return
	}
	_ = unlock(l.f)
	_ = l.f.Close()
	l.f = nil
}

// HeldElsewhere 这把锁现在被别的进程（或本进程别的句柄）拿着。
func HeldElsewhere(path string) bool {
	l := NewFileLock(path)
	ok, err := l.Acquire(false, 0)
	if err != nil {
		return false
	}
	if ok {
		l.Release()
		return false
	}
	return true
}

var errLocked = errors.New("锁被占着")
