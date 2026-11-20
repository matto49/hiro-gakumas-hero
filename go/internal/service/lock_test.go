package service

import (
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestFileLockIsExclusive(t *testing.T) {
	p := filepath.Join(t.TempDir(), "x.lock")
	a := NewFileLock(p)
	if ok, err := a.Acquire(false, 0); !ok || err != nil {
		t.Fatalf("first %v %v", ok, err)
	}
	if !HeldElsewhere(p) {
		t.Fatal("should be held")
	}
	b := NewFileLock(p)
	if ok, _ := b.Acquire(true, 150*time.Millisecond); ok {
		t.Fatal("second acquire should time out")
	}
	a.Release()
	if HeldElsewhere(p) {
		t.Fatal("released")
	}
}

// Python 版拿着锁时，Go 版要拿不到（两边认同一把锁）。
func TestLockInteropWithPython(t *testing.T) {
	py, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("没有 python3")
	}
	p := filepath.Join(t.TempDir(), "service.lock")
	cmd := exec.Command(py, "-c", `
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("locked", flush=True)
time.sleep(30)
`, p)
	out, _ := cmd.StdoutPipe()
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	buf := make([]byte, 6)
	if _, err := out.Read(buf); err != nil || string(buf) != "locked" {
		t.Fatalf("python lock: %q %v", buf, err)
	}
	if !HeldElsewhere(p) {
		t.Fatal("Go should see Python's lock")
	}
}
