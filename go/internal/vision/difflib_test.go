package vision

import (
	"os/exec"
	"strconv"
	"strings"
	"testing"
)

func TestSeqRatio(t *testing.T) {
	pairs := [][2]string{{"十王星南", "+王星南"}, {"abcde", "abxde"}, {"", ""}, {"篠澤広", "篠沢ひろ"}, {strings.Repeat("ab", 150) + "x", strings.Repeat("ba", 140) + "yx"}, {"やる気+3、好印象+2", "好印象+2、やる気+3を獲得"}}
	for _, p := range pairs {
		out, err := exec.Command("python3", "-c", "import sys,difflib;print(repr(difflib.SequenceMatcher(None,sys.argv[1],sys.argv[2]).ratio()))", p[0], p[1]).Output()
		if err != nil {
			t.Skip(err)
		}
		want, _ := strconv.ParseFloat(strings.TrimSpace(string(out)), 64)
		if got := seqRatio(p[0], p[1]); got != want {
			t.Errorf("%q %q: %v vs %v", p[0], p[1], got, want)
		}
	}
}
