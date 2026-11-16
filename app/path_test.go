package main

import "testing"

func TestMergePathKeepsOrderAndDropsDuplicates(t *testing.T) {
	got := mergePath("/usr/bin:/bin", "/opt/homebrew/bin:/usr/bin:/Users/x/.local/bin:")
	if got != "/usr/bin:/bin:/opt/homebrew/bin:/Users/x/.local/bin" {
		t.Fatalf("got %q", got)
	}
}
