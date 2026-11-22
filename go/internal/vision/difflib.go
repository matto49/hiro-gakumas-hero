package vision

// seqRatio 同 Python difflib.SequenceMatcher(None, a, b).ratio()：2·匹配字符数 / (len(a) + len(b))，
// 匹配块按 find_longest_match 递归找（b 长度 ≥ 200 时出现过多的字符不参与起点匹配，同 autojunk）。按 Unicode 码点算。
func seqRatio(as, bs string) float64 {
	a, b := []rune(as), []rune(bs)
	la, lb := len(a), len(b)
	if la+lb == 0 {
		return 1
	}
	b2j := map[rune][]int{}
	for j, r := range b {
		b2j[r] = append(b2j[r], j)
	}
	if lb >= 200 {
		ntest := lb/100 + 1
		for r, idx := range b2j {
			if len(idx) > ntest {
				delete(b2j, r)
			}
		}
	}
	longest := func(alo, ahi, blo, bhi int) (int, int, int) {
		bi, bj, bs := alo, blo, 0
		j2len := map[int]int{}
		for i := alo; i < ahi; i++ {
			nj := map[int]int{}
			for _, j := range b2j[a[i]] {
				if j < blo {
					continue
				}
				if j >= bhi {
					break
				}
				k := j2len[j-1] + 1
				nj[j] = k
				if k > bs {
					bi, bj, bs = i-k+1, j-k+1, k
				}
			}
			j2len = nj
		}
		for bi > alo && bj > blo && a[bi-1] == b[bj-1] {
			bi, bj, bs = bi-1, bj-1, bs+1
		}
		for bi+bs < ahi && bj+bs < bhi && a[bi+bs] == b[bj+bs] {
			bs++
		}
		return bi, bj, bs
	}
	matches := 0
	queue := [][4]int{{0, la, 0, lb}}
	for len(queue) > 0 {
		q := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		i, j, k := longest(q[0], q[1], q[2], q[3])
		if k == 0 {
			continue
		}
		matches += k
		if q[0] < i && q[2] < j {
			queue = append(queue, [4]int{q[0], i, q[2], j})
		}
		if i+k < q[1] && j+k < q[3] {
			queue = append(queue, [4]int{i + k, q[1], j + k, q[3]})
		}
	}
	return 2 * float64(matches) / float64(la+lb)
}
