package plugin

import (
	"context"
	"strings"
	"sync"
	"time"
)

// MaxContextRunes 是每个插件那段上下文最多多少字（§7）。
const MaxContextRunes = 1500

// ContextTimeout 是每个插件算上下文最多等多久。
const ContextTimeout = 2 * time.Second

// Gather 并行跑每个插件的 Context，按注册顺序拼成「## 现在的情况」；超时的、空的跳过；都没有时返回 ""。
func Gather(ctx context.Context, r *Registry, h Host, now time.Time, timeout time.Duration) string {
	ps := r.All()
	parts := make([]string, len(ps))
	var wg sync.WaitGroup
	for i, p := range ps {
		if p.Context == nil {
			continue
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			cctx, cancel := context.WithTimeout(ctx, timeout)
			defer cancel()
			done := make(chan string, 1)
			go func() { done <- p.Context(cctx, h) }()
			select {
			case s := <-done:
				if cctx.Err() == nil {
					parts[i] = truncate(strings.TrimSpace(s), MaxContextRunes)
				}
			case <-cctx.Done():
			}
		}()
	}
	wg.Wait()
	var b strings.Builder
	for i, p := range ps {
		if parts[i] == "" {
			continue
		}
		title := p.ContextTitle
		if title == "" {
			title = p.Name
		}
		b.WriteString("\n### " + title + "\n" + parts[i] + "\n")
	}
	if b.Len() == 0 {
		return ""
	}
	return "## 现在的情况（" + now.Format("01-02 15:04") + "）\n" + strings.TrimLeft(b.String(), "\n")
}

func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}
