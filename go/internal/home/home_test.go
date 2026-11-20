package home

import (
	"os"
	"path/filepath"
	"testing"
)

func TestDirFollowsEnv(t *testing.T) {
	d := filepath.Join(t.TempDir(), "h")
	t.Setenv("HIRO_HOME", d)
	if Dir() != d {
		t.Fatalf("Dir() = %q", Dir())
	}
	sub, err := Sub("pending")
	if err != nil {
		t.Fatal(err)
	}
	if st, err := os.Stat(sub); err != nil || !st.IsDir() || sub != filepath.Join(d, "pending") {
		t.Fatalf("Sub = %q, %v", sub, err)
	}
}
