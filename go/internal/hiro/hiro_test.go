package hiro

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var skill = filepath.Join("..", "..", "..", "skills", "hiro-cli", "SKILL.md")

// 同 tests/cli/test_skill_doc.py：每条命令都写进 skill 文档，外部 agent 才知道能用。
func TestEveryCommandIsDocumented(t *testing.T) {
	b, err := os.ReadFile(skill)
	if err != nil {
		t.Fatal(err)
	}
	text := string(b)
	var missing []string
	for _, c := range Registry().Commands() {
		if !strings.Contains(text, "hiro "+c.Name) {
			missing = append(missing, c.Name)
		}
	}
	if len(missing) > 0 {
		t.Fatalf("SKILL.md 里没写：%v", missing)
	}
}

func TestPendingProtocolAndRedLinesAreStated(t *testing.T) {
	b, _ := os.ReadFile(skill)
	text := string(b)
	if !strings.HasPrefix(text, "---\nname: hiro-cli\n") {
		t.Fatal("frontmatter")
	}
	for _, w := range []string{"待确认", "先问用户", "hiro confirm", "ジュエル", "放弃培育", "secret copy", "不显示"} {
		if !strings.Contains(text, w) {
			t.Fatalf("missing %q", w)
		}
	}
}

func TestNoForbiddenCommands(t *testing.T) {
	for _, c := range Registry().Commands() {
		for _, w := range []string{"jewel", "discard", "retire"} {
			if strings.Contains(c.Name, w) {
				t.Fatalf("%s", c.Name)
			}
		}
	}
}

func TestSameCommandsAsPython(t *testing.T) {
	want := []string{
		"tools", "confirm", "cancel", "pending list",
		"info summary", "info supports", "info memories", "info idols", "info affection", "info game-settings", "info import",
		"catalog sync", "catalog search", "catalog get", "catalog sprite",
		"run start", "run status", "run events", "run pause", "run takeover", "run resume", "run stop", "run report", "run review",
		"secret set", "secret list", "secret copy", "secret delete", "doctor", "plan formation", "plan show",
	}
	var got []string
	for _, c := range Registry().Commands() {
		got = append(got, c.Name)
	}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("\n got %v\nwant %v", got, want)
	}
}
