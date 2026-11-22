package vision

import (
	"bytes"
	"math"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"

	"github.com/matto49/hiro-gakumas-hero/go/internal/cv"
	"github.com/matto49/hiro-gakumas-hero/go/internal/img"
	"github.com/matto49/hiro-gakumas-hero/go/internal/master"
	"github.com/matto49/hiro-gakumas-hero/go/internal/ocr"
)

// pages.py 的后半（同 Python hiro.vision.pages）：弹窗、整帧文字页面地图 classify_page、本战名次 / 结算、开始确认。

var dialogKinds = [][2]string{
	{"ターン終了", "turn_end"},
	{"使用確認", "drink_use_confirm"},
	{"ドリンク詳細", "drink_detail"},
	{"相談終了", "consult_end"},
	{"インターバル終了", "interval_end"},
	{"カスタマイズ確認", "customize_confirm"},
	{"カスタマイズを実行", "customize_confirm"},
	{"特別指導終了", "guidance_end"},
	{"カスタマイズ", "customize_confirm"},
	{"交換確認", "exchange"},
	{"削除", "delete"},
	{"リタイア", "retire"},
	{"破棄", "data_discard"},
	{"所持上限", "pdrink_max"},
	{"報酬スキップ", "reward_skip"},
	{"リフレッシュ", "refresh_confirm"},
	{"通信エラー", "comm_error"},
	{"通信工ラー", "comm_error"},
	{"再挑戦", "retry_confirm"},
	{"再開", "produce_resume"},
}

var (
	dialogBodyKinds = [][2]string{{"リタイア", "retire"}, {"破棄", "data_discard"}, {"削除しますか", "delete"}, {"交換しますか", "exchange"}}
	dangerKinds     = map[string]bool{"retire": true, "data_discard": true}
	dangerWords     = []string{"リタイア", "破棄"}
	confirmKinds    = map[string]bool{"exchange": true, "delete": true}
)

// Dialog 是橙黄标题条的弹窗。
type Dialog struct {
	Kind    string `json:"kind"`
	Title   string `json:"title"`
	Body    string `json:"body"`
	Danger  bool   `json:"danger"`
	Y       int    `json:"y"`
	Confirm any    `json:"-"`
}

// Fields 转成 Python 的字典（有 confirm 时带上）。
func (d *Dialog) Fields() map[string]any {
	m := map[string]any{"kind": d.Kind, "title": d.Title, "body": d.Body, "danger": d.Danger, "y": d.Y}
	if d.Confirm != nil {
		m["confirm"] = d.Confirm
	}
	return m
}

func ocrJoin(b *img.BGR, scale float64) (string, error) {
	items, err := ocr.OCR(b, scale, ocr.JaEn)
	if err != nil {
		return "", err
	}
	return strings.ReplaceAll(ocr.Joined(items, ""), " ", ""), nil
}

// ReadDialog 橙黄标题条的弹窗（有多个时取最下面那个）；没有标题条返回 nil。
func ReadDialog(b *img.BGR) (*Dialog, error) {
	bs := HeaderBands(b)
	for i := len(bs) - 1; i >= 0; i-- {
		y1, y2 := bs[i].Y1, bs[i].Y2
		if !titleWhite(b, y1, y2) {
			continue
		}
		title, err := ocrJoin(b.Slice(20, max(0, y1-4), 560, y2+4), 3)
		if err != nil {
			return nil, err
		}
		if title == "" && y2-y1 < titleRetryH {
			if title, err = ocrJoin(b.Slice(20, max(0, y1-titleRetryUp), 560, y2+4), 3); err != nil {
				return nil, err
			}
		}
		if title == "" {
			continue
		}
		kind := "other"
		for _, kv := range dialogKinds {
			if strings.Contains(title, kv[0]) {
				kind = kv[1]
				break
			}
		}
		c := b.Slice(20, y2, 700, min(b.H, y2+450))
		body := ""
		if size(c) > 0 {
			if body, err = ocrJoin(c, 3); err != nil {
				return nil, err
			}
		}
		if kind == "other" {
			for _, kv := range dialogBodyKinds {
				if strings.Contains(body, kv[0]) {
					kind = kv[1]
					break
				}
			}
		}
		danger := dangerKinds[kind]
		if !danger && kind != "produce_resume" {
			for _, w := range dangerWords {
				if strings.Contains(title, w) || strings.Contains(body, w) {
					danger = true
				}
			}
		}
		return &Dialog{Kind: kind, Title: title, Body: body, Danger: danger, Y: floorDiv(y1+y2, 2)}, nil
	}
	return nil, nil
}

// OverlayDialog 页面上盖着的确认框：先像素判标题条，有再 ReadDialog；refresh_confirm 另读 confirm。
func OverlayDialog(b *img.BGR) (map[string]any, error) {
	if DialogHeader(b) == nil {
		return nil, nil
	}
	d, err := ReadDialog(b)
	if err != nil || d == nil {
		return nil, err
	}
	if d.Kind == "refresh_confirm" {
		if d.Confirm, err = ReadRefreshConfirm(b, &d.Y); err != nil {
			return nil, err
		}
	}
	return d.Fields(), nil
}

func ocrShifted(b *img.BGR, y0, y1 int) ([]TextBox, error) {
	items, err := ocr.Boxes(b.Slice(0, y0, b.W, y1), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	return shift(items, 0, y0), nil
}

// lowestButton 文字满足 match 的框里最靠下的一个（同 Python max(..., key=b[1])，并列取先出现的）的中心。
func lowestButton(boxes []TextBox, match func(string) bool) []int {
	var best *ocr.Box
	for i, tb := range boxes {
		if match(tb.Text) && (best == nil || tb.Box.Y1 > best.Y1) {
			best = &boxes[i].Box
		}
	}
	if best == nil {
		return nil
	}
	return center(*best)
}

// ReadCommError 「通信エラー」→ {retry, title, close, forced_title, text}。
func ReadCommError(b *img.BGR, headerY *int) (map[string]any, error) {
	hy := 970
	if headerY != nil {
		hy = *headerY
	}
	y0 := max(0, hy+30)
	boxes, err := ocrShifted(b, y0, min(b.H, y0+330))
	if err != nil {
		return nil, err
	}
	btn := func(words ...string) any {
		c := lowestButton(boxes, func(t string) bool {
			for _, w := range words {
				if strings.Contains(t, w) {
					return true
				}
			}
			return false
		})
		if c == nil {
			return nil
		}
		return c
	}
	text := joinTexts(boxes)
	retry, cl := btn("リトライ"), btn("閉じる")
	return map[string]any{
		"retry": retry, "title": btn("タイトルへ", "タイトルヘ"), "close": cl, "text": text,
		"forced_title": retry == nil && (cl != nil || strings.Contains(text, "タイトルに戻")),
	}, nil
}

var (
	retryCancel = []int{215, 1160}
	retryOK     = []int{505, 1160}
	fracRe      = pyRe(`(\d+)/(\d+)`)
	fracFullRe  = pyRe(`^\d+/\d+$`)
	num1to5Re   = pyRe(`^\d{1,5}$`)
)

// ReadRetryConfirm 「再挑戦確認」→ {free, jewel, tickets_before, tickets_after, left, total, cancel, ok}。
func ReadRetryConfirm(b *img.BGR, headerY *int) (map[string]any, error) {
	hy := 570
	if headerY != nil {
		hy = *headerY
	}
	y0 := max(0, hy+30)
	boxes, err := ocrShifted(b, y0, min(b.H, y0+650))
	if err != nil {
		return nil, err
	}
	var own *ocr.Box
	for i, tb := range boxes {
		if strings.Contains(tb.Text, "所持") {
			own = &boxes[i].Box
			break
		}
	}
	type xv struct{ x, v int }
	var nums []xv
	for _, tb := range boxes {
		if num1to5Re.MatchString(tb.Text) && own != nil && abs(cy(tb.Box)-cy(*own)) <= 25 {
			nums = append(nums, xv{tb.Box.X1, mustAtoi(tb.Text)})
		}
	}
	sort.SliceStable(nums, func(i, j int) bool { return nums[i].x < nums[j].x || (nums[i].x == nums[j].x && nums[i].v < nums[j].v) })
	var frac []string
	for _, tb := range boxes {
		if fracFullRe.MatchString(tb.Text) {
			frac = fracRe.FindStringSubmatch(tb.Text)
			break
		}
	}
	button := func(word string, def []int) []int {
		if c := lowestButton(boxes, func(t string) bool { return t == word || strings.HasSuffix(t, word) }); c != nil {
			return c
		}
		return def
	}
	free, jewel := false, false
	for _, tb := range boxes {
		free = free || strings.Contains(tb.Text, "無料")
		jewel = jewel || strings.Contains(tb.Text, "ジュエル")
	}
	out := map[string]any{
		"free": free, "jewel": jewel, "tickets_before": nil, "tickets_after": nil, "left": nil, "total": nil,
		"cancel": button("キャンセル", retryCancel), "ok": button("決定", retryOK),
	}
	if len(nums) >= 2 {
		out["tickets_before"], out["tickets_after"] = nums[0].v, nums[len(nums)-1].v
	}
	if frac != nil {
		out["left"], out["total"] = mustAtoi(frac[1]), mustAtoi(frac[2])
	}
	return out, nil
}

// ---------------------------------------------------------------- 过渡帧、事件台词

var (
	examTitleRe  = pyRe(`^第\d回$`)
	examTitle2Re = pyRe(`^選抜試験\d?$`)
	eventBlock   = []string{"SKIP", "早送", "AUTO", "ログ", "キャンセル", "決定", "次へ", "閉じる", "OK", "選択", "受け取"}
	eventTap     = []int{360, 1030}
)

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

func transitionKind(boxes []TextBox) string {
	for _, tb := range boxes {
		if strings.Contains(strings.ReplaceAll(strings.ToUpper(tb.Text), ".", ""), "NOWLOADING") {
			return "loading"
		}
	}
	if hasAny(boxes, "パラメータボーナス") != nil || hasAny(boxes, "親愛度ボーナス") != nil {
		return "bonus_animation"
	}
	for _, tb := range boxes {
		if c := cy(tb.Box); strings.Contains(tb.Text, "試験") && strings.Contains(tb.Text, "まで") && 90 <= c && c <= 200 && hasAny(boxes, "審査基準") == nil {
			return "day_countdown"
		}
	}
	if has(boxes, "選抜試験", 650, 820) != nil {
		for _, tb := range boxes {
			if examTitleRe.MatchString(tb.Text) {
				return "exam_title"
			}
		}
	}
	for _, tb := range boxes {
		if c := cy(tb.Box); examTitle2Re.MatchString(tb.Text) && tb.Box.H() >= 40 && 560 <= c && c <= 720 {
			return "exam_title"
		}
	}
	for _, tb := range boxes {
		if c := cy(tb.Box); (tb.Text == "突破" || tb.Text == "敗退") && tb.Box.H() >= 60 && 820 <= c && c <= 1000 {
			return "exam_result_anim"
		}
	}
	return ""
}

// EventDialogue 事件台词（台词框上方有名字条，没有 SKIP / 早送り 底栏、没有按钮字样）→ commu 字段；不是返回 nil。
func EventDialogue(boxes []TextBox) map[string]any {
	for _, tb := range boxes {
		for _, w := range eventBlock {
			t := tb.Text
			if isASCII(w) {
				t = strings.ToUpper(t)
			}
			if strings.Contains(t, w) {
				return nil
			}
		}
	}
	for _, tb := range boxes {
		if c := cy(tb.Box); 1070 <= c && c <= 1170 {
			return nil
		}
	}
	speaker := ""
	found := false
	for _, tb := range boxes {
		n := utf8.RuneCountInString(pyStrip(tb.Text))
		if c := cy(tb.Box); n >= 1 && n <= 8 && tb.Box.X2 < 300 && 880 <= c && c <= 980 {
			speaker, found = tb.Text, true
			break
		}
	}
	var lines []string
	for _, tb := range boxes {
		if c := cy(tb.Box); 960 <= c && c <= 1130 && tb.Box.X1 < 200 && utf8.RuneCountInString(pyStrip(tb.Text)) >= 6 {
			lines = append(lines, tb.Text)
		}
	}
	if !found || len(lines) == 0 {
		return nil
	}
	fields := map[string]any{"kind": "event_dialogue", "speaker": pyStrip(speaker), "text": strings.Join(lines, "")}
	out := map[string]any{"_quick": map[string]any{"page": "commu", "tap": eventTap, "dispatch": nil, "fields": fields}}
	for k, v := range fields {
		out[k] = v
	}
	return out
}

// ---------------------------------------------------------------- 本战开始确认

func isDigitRune(r rune) bool { return digitValue(r) >= 0 }

func isPySpace(r rune) bool { return pySpace(r) }

// growthPct 成长率「45.5%」（总是一位小数）；小数点读成「:」「,」或读丢时也认。
func growthPct(text string) (float64, bool) {
	rs := []rune(text)
	n := len(rs)
	seps := "．.:,，"
	for i := range n {
		for _, nd := range []int{2, 1} {
			if i+nd > n {
				continue
			}
			ok := true
			for k := range nd {
				ok = ok && isDigitRune(rs[i+k])
			}
			if !ok {
				continue
			}
			j := i + nd
			for j < n && isPySpace(rs[j]) {
				j++
			}
			if j >= n || !strings.ContainsRune(seps, rs[j]) {
				continue
			}
			j++
			for j < n && isPySpace(rs[j]) {
				j++
			}
			if j >= n || !isDigitRune(rs[j]) {
				continue
			}
			if j+1 < n && isDigitRune(rs[j+1]) {
				continue
			}
			v, _ := strconv.ParseFloat(normDigits(string(rs[i:i+nd]))+"."+normDigits(string(rs[j])), 64)
			return v, true
		}
	}
	for i := 0; i+3 <= n; i++ {
		if i > 0 && isDigitRune(rs[i-1]) {
			continue
		}
		if !isDigitRune(rs[i]) || !isDigitRune(rs[i+1]) || !isDigitRune(rs[i+2]) {
			continue
		}
		j := i + 3
		for j < n && isPySpace(rs[j]) {
			j++
		}
		if j < n && rs[j] == '%' {
			v, _ := strconv.ParseFloat(normDigits(string(rs[i:i+2]))+"."+normDigits(string(rs[i+2])), 64)
			return v, true
		}
	}
	return 0, false
}

var (
	fsNumRe  = pyRe(`^\D?\d{2,4}$`)
	fsStarRe = pyRe(`^\d{2,4}$`)
)

// ReadFinalsStart 本战「開始確認」页：当前选中的那份选拔回忆的概要。
func ReadFinalsStart(boxes []TextBox, b *img.BGR) (map[string]any, error) {
	type xt struct {
		x int
		t string
	}
	row := func(lo, hi int, ok func(string) bool) []xt {
		var out []xt
		for _, tb := range boxes {
			if c := cy(tb.Box); lo <= c && c <= hi && ok(tb.Text) {
				out = append(out, xt{cx(tb.Box), tb.Text})
			}
		}
		sort.SliceStable(out, func(i, j int) bool { return out[i].x < out[j].x || (out[i].x == out[j].x && out[i].t < out[j].t) })
		return out
	}
	var nums []int
	for _, r := range row(482, 512, fsNumRe.MatchString) {
		nums = append(nums, mustAtoi(nonDigit.ReplaceAllString(r.t, "")))
	}
	var pcts []float64
	for _, r := range row(514, 540, anyDigitRe.MatchString) {
		if v, ok := growthPct(r.t); ok {
			pcts = append(pcts, v)
		}
	}
	var starBox *ocr.Box
	for i, tb := range boxes {
		if c := cy(tb.Box); 430 <= c && c <= 465 && fsStarRe.MatchString(tb.Text) && 440 <= cx(tb.Box) && cx(tb.Box) <= 600 {
			starBox = &boxes[i].Box
			break
		}
	}
	var star any
	for _, r := range row(430, 465, fsStarRe.MatchString) {
		if 440 <= r.x && r.x <= 600 {
			star = mustAtoi(r.t)
			break
		}
	}
	if eng, _ := ocr.Engine(); starBox != nil && b != nil && eng != "vision" {
		if d, ok, err := ReadDddd(b, *starBox); err != nil {
			return nil, err
		} else if ok && d >= 10 && d <= 9999 {
			star = d
		}
	}
	var idol any
	for _, tb := range boxes {
		if c := cy(tb.Box); tb.Box.X1 >= 480 && 150 <= c && c <= 230 && !anyDigitRe.MatchString(tb.Text) {
			idol = tb.Text
			break
		}
	}
	var params, growth any
	if len(nums) == 3 {
		params = map[string]any{"Vo": nums[0], "Da": nums[1], "Vi": nums[2]}
	}
	if len(pcts) == 3 {
		growth = map[string]any{"Vo": pcts[0], "Da": pcts[1], "Vi": pcts[2]}
	}
	return map[string]any{
		"selected_memory":  map[string]any{"idol": idol, "star": star, "params": params, "growth_pct": growth, "deck": nil, "pitems": nil},
		"qualify_memories": nil,
	}, nil
}

// ---------------------------------------------------------------- 本战名次 / 结算

var (
	finalsNpcBands = map[int][][2]int{1: {{397_457, 409_161}, {293_847, 322_182}}, 2: {{590_229, 618_779}, {417_672, 507_672}}}
	resultRetry    = ocr.Box{X1: 10, Y1: 1050, X2: 250, Y2: 1180}
	retryLeftRe    = pyRe(`あと(\d+)回`)
	resultRowY     = []int{324, 503, 682}
)

const resultScoreMax = 2_999_999

func npcRound(pos, v int) int {
	for _, r := range []int{1, 2} {
		bands := finalsNpcBands[r]
		if pos < len(bands) && bands[pos][0] <= v && v <= bands[pos][1] {
			return r
		}
	}
	return 0
}

// FinalsRoundFromNpc 按 NPC 目标分判本战第几轮（两位都归上了必须是同一轮）；判不出返回 nil。
func FinalsRoundFromNpc(npc []*int) any {
	rounds := map[int]bool{}
	for i, v := range npc {
		if i >= 2 {
			break
		}
		if v != nil {
			if r := npcRound(i, *v); r != 0 {
				rounds[r] = true
			}
		}
	}
	if len(rounds) == 1 {
		for r := range rounds {
			return r
		}
	}
	return nil
}

// ReadRetry 结算页左下「再挑戦」：{left, free}；没有「再挑戦」按钮返回 nil。
func ReadRetry(b *img.BGR) (map[string]any, error) {
	items, err := ocr.OCR(crop(b, resultRetry), 3, ocr.JaEn)
	if err != nil {
		return nil, err
	}
	has := false
	var badge strings.Builder
	for _, it := range items {
		t := strings.ReplaceAll(it.Text, " ", "")
		has = has || strings.Contains(t, "再挑戦")
		if strings.Contains(t, "回") {
			badge.WriteString(t)
		}
	}
	if !has {
		return nil, nil
	}
	bs := badge.String()
	out := map[string]any{"left": nil, "free": nil}
	if m := retryLeftRe.FindStringSubmatch(bs); m != nil {
		out["left"] = mustAtoi(m[1])
	}
	if bs != "" {
		out["free"] = strings.Contains(bs, "無料")
	}
	return out, nil
}

// pySetOrder CPython 小集合（≤ 4 个非负整数，8 格散列表）的迭代顺序：投票并列时 max 取先遍历到的。
func pySetOrder(vals []int) []int {
	var uniq []int
	seen := map[int]bool{}
	for _, v := range vals {
		if !seen[v] {
			seen[v] = true
			uniq = append(uniq, v)
		}
	}
	if len(uniq) > 4 {
		sort.Ints(uniq)
		return uniq
	}
	var table [8]*int
	for k := range uniq {
		v := uniq[k]
		h := uint64(v) //nolint:gosec // 非负分数
		i, perturb := h&7, h
		for table[i] != nil {
			perturb >>= 5
			i = (i*5 + 1 + perturb) & 7
		}
		table[i] = &uniq[k]
	}
	out := make([]int, 0, len(uniq))
	for _, p := range table {
		if p != nil {
			out = append(out, *p)
		}
	}
	return out
}

// VoteTruncated 长数字多种读法投票（读数 a 支持 b ⇔ 相等、a 是 b 掉了末位、或 a 是 b 掉了开头几位）；支持最多的胜出，并列取更长的。
func VoteTruncated(votes []*int) (int, int, bool) {
	var vals []int
	for _, v := range votes {
		if v != nil {
			vals = append(vals, *v)
		}
	}
	if len(vals) == 0 {
		return 0, 0, false
	}
	supports := func(a, b int) bool {
		sa, sb := itoa(a), itoa(b)
		return a == b || (len(sa) == len(sb)-1 && strings.HasPrefix(sb, sa)) || (len(sa) < len(sb) && strings.HasSuffix(sb, sa))
	}
	best, bn, bl := 0, -1, -1
	for _, b := range pySetOrder(vals) {
		n := 0
		for _, a := range vals {
			if supports(a, b) {
				n++
			}
		}
		if l := len(itoa(b)); n > bn || (n == bn && l > bl) {
			best, bn, bl = b, n, l
		}
	}
	return best, bn, true
}

func ptrOrNil(s string) *int {
	if s == "" {
		return nil
	}
	v, ok := pyAtoi(s)
	if !ok {
		return nil
	}
	return &v
}

// resultScore 结算页一行分数：名单整块 OCR、这一格放大再 OCR、ddddocr 三次读数投票，至少两票支持才给。
func resultScore(b *img.BGR, listed string, box ocr.Box) (*int, error) {
	c := b.Slice(max(0, box.X1-6), max(0, box.Y1-6), min(b.W, box.X2+30), box.Y2+6)
	t, err := ocrJoin(cv.ResizeScale(c, 3, cv.Cubic), 3)
	if err != nil {
		return nil, err
	}
	head, _, _ := strings.Cut(t, "P")
	again := nonDigit.ReplaceAllString(head, "")
	d, ok, err := ReadDddd(b, ocr.Box{X1: max(0, box.X1-4), Y1: max(0, box.Y1-4), X2: box.X2 + 4, Y2: box.Y2 + 4})
	if err != nil {
		return nil, err
	}
	var dv *int
	if ok {
		dv = &d
	}
	best, n, okv := VoteTruncated([]*int{ptrOrNil(listed), ptrOrNil(again), dv})
	if okv && n >= 2 {
		return &best, nil
	}
	return nil, nil
}

// counterVote Counter(非空票).most_common(1)：票数最多的，并列取先出现的；至少两票才给。
func counterVote(votes ...string) *int {
	counts := map[string]int{}
	var order []string
	for _, v := range votes {
		if v == "" {
			continue
		}
		if _, ok := counts[v]; !ok {
			order = append(order, v)
		}
		counts[v]++
	}
	if len(order) == 0 {
		return nil
	}
	best := order[0]
	for _, v := range order[1:] {
		if counts[v] > counts[best] {
			best = v
		}
	}
	if counts[best] < 2 {
		return nil
	}
	return ptrOrNil(best)
}

func gainCut(b *img.BGR, box ocr.Box, nTotal, nGain int) int {
	start := box.X1 + floorDiv((box.X2-box.X1)*35, 100)
	hsv := cv.HSV(b.Slice(start, box.Y1+8, box.X2, box.Y2-8))
	for x := range hsv.W {
		k := 0
		for y := range hsv.H {
			if p := hsv.Pix[(y*hsv.W+x)*3:]; p[0] >= 90 && p[0] <= 115 && p[1] >= 120 && p[2] >= 150 {
				k++
			}
		}
		if k >= 3 {
			return start + x - 2
		}
	}
	return box.X1 + int(math.RoundToEven(float64(box.X2-box.X1)*float64(nTotal)/float64(nTotal+nGain))) + 4
}

var plusSplitRe = pyRe(`[+＋]`)

func mergedGain(b *img.BGR, listed string, box ocr.Box, nTotal, nGain int) (*int, error) {
	cut := gainCut(b, box, nTotal, nGain)
	c := b.Slice(cut, max(0, box.Y1-6), min(b.W, box.X2+6), box.Y2+6)
	t, err := ocrJoin(cv.ResizeScale(c, 3, cv.Cubic), 1)
	if err != nil {
		return nil, err
	}
	again := nonDigit.ReplaceAllString(t, "")
	d, ok, err := ReadDddd(b, ocr.Box{X1: cut + 14, Y1: max(0, box.Y1-4), X2: min(b.W, box.X2+4), Y2: box.Y2 + 4})
	if err != nil {
		return nil, err
	}
	ds := ""
	if ok {
		ds = itoa(d)
	}
	if ds != "" && listed != "" && len(ds) == utf8.RuneCountInString(listed)+1 && ds[0] == '4' {
		ds = ds[1:]
	}
	if ds != "" && listed != "" && len(ds) == utf8.RuneCountInString(listed) {
		return ptrOrNil(ds), nil
	}
	return counterVote(listed, again, ds), nil
}

func mergedTotal(b *img.BGR, listed string, box ocr.Box, nTotal, nGain int) (*int, error) {
	cut := gainCut(b, box, nTotal, nGain)
	c := b.Slice(max(0, box.X1-6), max(0, box.Y1-6), cut, box.Y2+6)
	t, err := ocrJoin(cv.ResizeScale(c, 3, cv.Cubic), 1)
	if err != nil {
		return nil, err
	}
	again := nonDigit.ReplaceAllString(plusSplitRe.Split(t, 2)[0], "")
	d, ok, err := ReadDddd(b, ocr.Box{X1: max(0, box.X1-4), Y1: max(0, box.Y1-4), X2: cut, Y2: box.Y2 + 4})
	if err != nil {
		return nil, err
	}
	ds := ""
	if ok {
		ds = itoa(d)
	}
	return counterVote(listed, again, ds), nil
}

var twoDigitsRe = pyRe(`\d{2}`)

func idolName(text string) any {
	text = strings.ReplaceAll(text, " ", "")
	best, bs := "", -1.0
	for _, n := range master.CharacterNames() {
		if r := seqRatio(text, n); r > bs {
			best, bs = n, r
		}
	}
	if best != "" && bs >= 0.6 {
		return best
	}
	return nil
}

func rowName(boxes []TextBox, score ocr.Box) any {
	c := cy(score)
	var hits []TextBox
	for _, tb := range boxes {
		if d := c - cy(tb.Box); 200 <= tb.Box.X1 && tb.Box.X1 <= 420 && 25 <= d && d <= 90 && !twoDigitsRe.MatchString(tb.Text) && utf8.RuneCountInString(tb.Text) >= 2 {
			hits = append(hits, tb)
		}
	}
	if len(hits) == 0 {
		return nil
	}
	best := hits[0]
	for _, h := range hits[1:] {
		if abs(c-cy(h.Box)-55) < abs(c-cy(best.Box)-55) {
			best = h
		}
	}
	return idolName(best.Text)
}

func rankByRowY(c int) int {
	for i, y := range resultRowY {
		if abs(c-y) <= 40 {
			return i + 1
		}
	}
	return 0
}

var gainBoxRe = pyRe(`^[+＋十1]?\s*(\d{1,3}(?:[,、]\d{3})+)$`)

func splitGainBox(boxes []TextBox, idx int) (string, ocr.Box) {
	b := boxes[idx].Box
	c := float64(b.Y1+b.Y2) / 2
	for i, tb := range boxes {
		b2 := tb.Box
		if i == idx || !(-25 <= b2.X1-b.X2 && b2.X1-b.X2 <= 30 && b2.X2 > b.X2 && math.Abs(float64(b2.Y1+b2.Y2)/2-c) <= 15) {
			continue
		}
		if g := gainBoxRe.FindStringSubmatch(strings.ReplaceAll(tb.Text, " ", "")); g != nil {
			return strings.ReplaceAll(g[1], "、", ","), ocr.Box{X1: b.X1, Y1: min(b.Y1, b2.Y1), X2: b2.X2, Y2: max(b.Y2, b2.Y2)}
		}
	}
	return "", b
}

var (
	roundRe      = pyRe(`ラウンド\s*(\d)`)
	commaFixRe   = pyRe(`[、，.]`)
	roundRowRe   = pyRe(`^(\d{1,3}(?:,\d{3})+)(?:\s*[^\d,\s]{1,2}\s*(\d{1,3}(?:,\d{3})+|\d{1,7}))?$`)
	rankDigitRe  = pyRe(`^[1-9]$`)
	standingPtRe = pyRe(`([\d,]{3,})\s*[pP][tT]`)
)

func readRoundResult(b *img.BGR, boxes []TextBox, title string) (map[string]any, error) {
	m := roundRe.FindStringSubmatch(title)
	var log *ocr.Box
	for i, tb := range boxes {
		if strings.HasPrefix(tb.Text, "ログ") {
			log = &boxes[i].Box
			break
		}
	}
	finalPage := m == nil
	type row struct {
		b           ocr.Box
		total, gain string
	}
	var rows []row
	for i, tb := range boxes {
		t := commaFixRe.ReplaceAllString(tb.Text, ",")
		mm := roundRowRe.FindStringSubmatch(t)
		if mm == nil || tb.Box.X1 < 200 || tb.Box.X1 > 420 {
			continue
		}
		bx, gain := tb.Box, mm[2]
		if gain == "" && finalPage {
			gain, bx = splitGainBox(boxes, i)
		}
		rows = append(rows, row{bx, mm[1], gain})
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].b.Y1 < rows[j].b.Y1 })
	type dy struct{ d, y int }
	var digits []dy
	for _, tb := range boxes {
		if rankDigitRe.MatchString(tb.Text) && tb.Box.X2 <= 140 {
			digits = append(digits, dy{mustAtoi(tb.Text), cy(tb.Box)})
		}
	}
	entries := []map[string]any{}
	var me map[string]any
	for i, r := range rows {
		c := cy(r.b)
		rank := 0
		for _, d := range digits {
			if abs(d.y-c) <= 60 {
				rank = d.d
				break
			}
		}
		if rank == 0 {
			rank = rankByRowY(c)
		}
		if rank == 0 {
			rank = i + 1
		}
		total := nonDigit.ReplaceAllString(r.total, "")
		nt, ng := utf8.RuneCountInString(r.total), utf8.RuneCountInString(r.gain)+1
		var score *int
		var err error
		if r.gain != "" {
			score, err = mergedTotal(b, total, r.b, nt, ng)
		} else {
			score, err = resultScore(b, total, r.b)
		}
		if err != nil {
			return nil, err
		}
		if score != nil && *score > resultScoreMax {
			score = nil
		}
		isMe := log != nil && abs(cy(*log)-c) <= 50
		var g *int
		if r.gain != "" {
			if g, err = mergedGain(b, nonDigit.ReplaceAllString(r.gain, ""), r.b, nt, ng); err != nil {
				return nil, err
			}
		}
		var rs any
		switch {
		case g != nil:
			rs = *g
		case m != nil && m[1] == "1" && score != nil:
			rs = *score
		}
		e := map[string]any{"rank": rank, "name": rowName(boxes, r.b), "score": anyInt(score), "gain": anyInt(g), "me": isMe, "round_score": rs}
		entries = append(entries, e)
		if isMe && me == nil {
			me = e
		}
	}
	var outcome any
	for _, tb := range boxes {
		if (tb.Text == "敗退" || tb.Text == "突破" || tb.Text == "優勝" || tb.Text == "準優勝") && tb.Box.Y1 < 160 {
			outcome = tb.Text
			break
		}
	}
	retry, err := ReadRetry(b)
	if err != nil {
		return nil, err
	}
	out := map[string]any{
		"kind": "final", "score": nil, "round_score": nil, "entries": entries, "next": false, "my_score": nil, "rank": nil, "my_gain": nil,
		"round_result": nil, "final": finalPage, "outcome": outcome, "retry": anyMap(retry), "passed": nil,
	}
	if me != nil {
		out["score"], out["round_score"], out["my_score"], out["rank"], out["my_gain"] = me["score"], me["round_score"], me["score"], me["rank"], me["gain"]
	}
	if m != nil {
		rnd := mustAtoi(m[1])
		out["kind"], out["round_result"], out["finals_round"] = "round_result", rnd, rnd
	} else {
		out["finals_round"] = 2
	}
	if s, ok := outcome.(string); ok {
		out["passed"] = map[string]bool{"敗退": false, "突破": true, "優勝": true, "準優勝": true}[s]
	}
	return out, nil
}

func anyInt(p *int) any {
	if p == nil {
		return nil
	}
	return *p
}

func anyMap(m map[string]any) any {
	if m == nil {
		return nil
	}
	return m
}

// ReadFinalsRankingPage 本战名次页「現在順位」（演出后的结果页交给 readRoundResult）。
func ReadFinalsRankingPage(b *img.BGR, boxes []TextBox) (map[string]any, error) {
	title := ""
	got := false
	for _, tb := range boxes {
		if strings.Contains(tb.Text, "ラウンド") && strings.Contains(tb.Text, "結果") {
			title, got = tb.Text, true
			break
		}
	}
	if got || hasAny(boxes, "ログ") != nil {
		return readRoundResult(b, boxes, title)
	}
	type ys struct{ y, s int }
	var rows []ys
	for _, tb := range boxes {
		if m := standingPtRe.FindStringSubmatch(tb.Text); m != nil {
			rows = append(rows, ys{cy(tb.Box), mustAtoi(nonDigit.ReplaceAllString(m[1], ""))})
		}
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].y < rows[j].y || (rows[i].y == rows[j].y && rows[i].s < rows[j].s) })
	hsv := cv.HSV(b)
	entries := []map[string]any{}
	var me map[string]any
	var npc []*int
	for i, r := range rows {
		card := hsv.Slice(40, max(0, r.y-150), 200, max(1, r.y-20))
		isMe := fracWhere(card, func(p []byte) bool { return p[0] >= 85 && p[0] <= 105 && p[1] >= 120 && p[2] >= 150 }) >= 0.3
		e := map[string]any{"rank": i + 1, "name": nil, "score": r.s, "me": isMe}
		entries = append(entries, e)
		if isMe && me == nil {
			me = e
		} else if !isMe {
			s := r.s
			npc = append(npc, &s)
		}
	}
	out := map[string]any{
		"kind": "standings", "score": nil, "round_score": nil, "entries": entries, "next": hasAny(boxes, "NEXT") != nil,
		"my_score": nil, "rank": nil, "finals_round": FinalsRoundFromNpc(npc), "round_result": nil, "retry": nil,
		"my_gain": nil, "final": false, "outcome": nil, "passed": nil,
	}
	if me != nil {
		out["score"], out["my_score"], out["rank"] = me["score"], me["score"], me["rank"]
	}
	return out, nil
}

// ---------------------------------------------------------------- 整帧文字页面地图

var (
	dayLogRe  = pyRe(`\d+日目$`)
	pageMu    sync.Mutex
	pageKey   []byte
	pageCache *PageResult
	pageValid bool
)

// PageResult 是 classify_page 的结果：页面名和字段。
type PageResult struct {
	Page   string
	Fields map[string]any
}

// ClassifyPage 模板认不出的帧 → (页面名, 识别字段) 或 nil。同一帧（缩略图相同）连续调用直接复用。
func ClassifyPage(b *img.BGR) (*PageResult, error) {
	key := cv.Resize(b, 45, 80, cv.Area).Pix
	pageMu.Lock()
	if pageValid && bytes.Equal(pageKey, key) {
		r := pageCache
		pageMu.Unlock()
		return r, nil
	}
	pageMu.Unlock()
	r, err := classifyPage(b)
	if err != nil {
		return nil, err
	}
	pageMu.Lock()
	pageKey, pageCache, pageValid = key, r, true
	pageMu.Unlock()
	return r, nil
}

// AcquireOver 已判成某页（相談）但上面盖着获得演出时用：只读名称栏。
func AcquireOver(b *img.BGR) (map[string]any, error) { return ReadAcquirePopup(b, true) }

func classifyPage(b *img.BGR) (*PageResult, error) {
	res := func(p string, f map[string]any) (*PageResult, error) { return &PageResult{p, f}, nil }
	dialog, err := ReadDialog(b)
	if err != nil {
		return nil, err
	}
	if dialog != nil {
		boxes, err := pageTexts(b)
		if err != nil {
			return nil, err
		}
		if p, f := TowerDialog(dialog, boxes); p != "" {
			return res(p, f)
		}
		return res("DIALOG", dialog.Fields())
	}
	boxes, err := pageTexts(b)
	if err != nil {
		return nil, err
	}
	if p, f, err := TowerPage(b, boxes); err != nil || p != "" {
		if err != nil {
			return nil, err
		}
		return res(p, f)
	}
	for _, tb := range boxes {
		if c := cy(tb.Box); acquireBar.Y1 <= c && c <= acquireBar.Y2 {
			acq, err := ReadAcquirePopup(b, true)
			if err != nil {
				return nil, err
			}
			if acq != nil {
				return res("ACQUIRE", acq)
			}
			break
		}
	}
	texts := make([]string, len(boxes))
	for i, tb := range boxes {
		texts[i] = tb.Text
	}
	switch {
	case has(boxes, "開始確認", 0, 90) != nil && hasAny(boxes, "選抜試験メモリー") != nil && hasAny(boxes, "本戦へ") != nil:
		f, err := ReadFinalsStart(boxes, b)
		if err != nil {
			return nil, err
		}
		return res("HIF_FINALS_START", f)
	case has(boxes, "現在順位", 100, 300) != nil:
		f, err := ReadFinalsRankingPage(b, boxes)
		if err != nil {
			return nil, err
		}
		return res("HIF_FINALS_RANKING", f)
	case has(boxes, "スター性獲得", 280, 520) != nil:
		f, err := ReadStarGain(b)
		if err != nil {
			return nil, err
		}
		return res("STAR_GAIN", f)
	case (hasAny(boxes, "突破ライン") != nil && hasAny(boxes, "合格条件") != nil) || hasAny(boxes, "優勝条件") != nil:
		f, err := ReadExamBriefing(b, boxes)
		if err != nil {
			return nil, err
		}
		return res("HIF_EXAM_BRIEFING", f)
	case hasAny(boxes, "カスタマイズ完了") != nil:
		return res("PITEM_CUSTOMIZE_DONE", map[string]any{})
	}
	for _, t := range texts {
		if strings.Contains(t, "カスタム") && strings.Contains(t, "選択して") {
			return res("PITEM_CUSTOMIZE", map[string]any{})
		}
	}
	if has(boxes, "完了", 40, 140) != nil && has(boxes, "プロデュース", 0, 120) != nil && hasAny(boxes, "ファン数") != nil {
		return res("PRODUCE_END", map[string]any{})
	}
	if has(boxes, "リタイア", 900, 1200) != nil && (has(boxes, "P手帳", 900, 1200) != nil || has(boxes, "中断", 900, 1200) != nil) {
		return res("MENU", map[string]any{"kind": "produce"})
	}
	for _, tb := range boxes {
		if dayLogRe.MatchString(tb.Text) && tb.Box.Y2 <= 220 {
			if fracWhere(cv.HSV(b.Slice(335, 1178, 385, 1196)), func(p []byte) bool { return p[1] < 40 && p[2] > 220 }) >= 0.6 {
				return res("PRODUCE_LOG", map[string]any{"close": []int{360, 1160}})
			}
			break
		}
	}
	done := strings.Join(texts, "")
	if strings.Contains(done, "カスタマイズしまし") {
		line := done
		for _, t := range texts {
			if strings.Contains(t, "をカスタマイズ") {
				line = t
				break
			}
		}
		return res("CUSTOMIZE_RESULT", ReadCustomizeResult(line))
	}
	if IsSpecialGuidance(boxes) {
		f, err := ReadSpecialGuidance(b, boxes)
		if err != nil {
			return nil, err
		}
		return res("SPECIAL_GUIDANCE", f)
	}
	if hasAny(boxes, "プロフィール") != nil && hasAny(boxes, "名刺") != nil {
		return res("MENU", map[string]any{"kind": "home"})
	}
	if has(boxes, "差し入れ", 0, 100) != nil {
		for _, tb := range boxes {
			if c := cy(tb.Box); tb.Box.H() >= 50 && 800 <= c && c <= 1000 {
				return res("GIFT", ReadGift(b))
			}
		}
	}
	for _, t := range texts {
		if strings.HasSuffix(t, "を強化しました") {
			return res("UPGRADE_RESULT", ReadUpgradeResult(t))
		}
	}
	if k := transitionKind(boxes); k != "" {
		return res("TRANSITION", map[string]any{"kind": k})
	}
	if ev := EventDialogue(boxes); ev != nil {
		return res("commu", ev)
	}
	return nil, nil
}

// PageFields extract 用：本模块页面的识别字段（与 ClassifyPage 判出的同一页面才有）。
func PageFields(page string, b *img.BGR) (map[string]any, error) {
	if page == "ACQUIRE" {
		acq, err := AcquireOver(b)
		if err != nil {
			return nil, err
		}
		if acq != nil {
			return acq, nil
		}
	}
	hit, err := ClassifyPage(b)
	if err != nil {
		return nil, err
	}
	if hit == nil || hit.Page != page {
		return map[string]any{"error": "画面不再是 " + page}, nil
	}
	f := hit.Fields
	if page == "DIALOG" {
		kind, _ := f["kind"].(string)
		var y *int
		if v, ok := f["y"].(int); ok {
			y = &v
		}
		var conf any
		switch {
		case confirmKinds[kind]:
			c, err := ReadConfirmDialog(b)
			if err != nil {
				return nil, err
			}
			conf = anyMap(c)
		case kind == "comm_error":
			if conf, err = ReadCommError(b, y); err != nil {
				return nil, err
			}
		case kind == "retry_confirm":
			if conf, err = ReadRetryConfirm(b, y); err != nil {
				return nil, err
			}
		case kind == "refresh_confirm":
			if conf, err = ReadRefreshConfirm(b, y); err != nil {
				return nil, err
			}
		default:
			return f, nil
		}
		out := map[string]any{}
		for k, v := range f {
			out[k] = v
		}
		out["confirm"] = conf
		return out, nil
	}
	return f, nil
}
