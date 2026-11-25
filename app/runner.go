package main

import (
	"sync"

	"github.com/matto49/hiro-gakumas-hero/app/shell"
)

// lateRunner 在窗口建好之前先排队，建好后交给窗口的 Update（界面要先有 Runner 才能建，窗口要先有界面才能建）。
type lateRunner struct {
	mu      sync.Mutex
	r       shell.Runner
	pending []func()
}

func (l *lateRunner) set(r shell.Runner) {
	l.mu.Lock()
	l.r = r
	p := l.pending
	l.pending = nil
	l.mu.Unlock()
	for _, fn := range p {
		r.Post(fn)
	}
}

func (l *lateRunner) Go(work func() func()) {
	go func() {
		if apply := work(); apply != nil {
			l.Post(apply)
		}
	}()
}

func (l *lateRunner) Post(fn func()) {
	l.mu.Lock()
	r := l.r
	if r == nil {
		l.pending = append(l.pending, fn)
	}
	l.mu.Unlock()
	if r != nil {
		r.Post(fn)
	}
}
