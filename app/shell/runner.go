package shell

import "sync"

// Runner 把后台工作和界面线程隔开：Go 在后台跑 work，work 返回的函数回到界面线程执行。
type Runner interface {
	Go(work func() func())
	// Post 把 fn 放回界面线程执行（后台 goroutine 里改状态用）。
	Post(fn func())
}

// WindowRunner 是窗口里的 Runner：goroutine + Window.Update。
type WindowRunner struct {
	// Update 是 mygo 窗口的 Update（在主线程跑 fn 并重画）。
	Update func(fn func())
}

func (w WindowRunner) Go(work func() func()) {
	go func() {
		if apply := work(); apply != nil {
			w.Update(apply)
		}
	}()
}

func (w WindowRunner) Post(fn func()) { w.Update(fn) }

// SyncRunner 是测试用的 Runner：工作先排队，Drain 时按顺序同步跑完（包括跑的过程中新排的）。
type SyncRunner struct {
	mu    sync.Mutex
	queue []func()
}

func (s *SyncRunner) Go(work func() func()) {
	s.push(func() {
		if apply := work(); apply != nil {
			apply()
		}
	})
}

func (s *SyncRunner) Post(fn func()) { s.push(fn) }

func (s *SyncRunner) push(fn func()) {
	s.mu.Lock()
	s.queue = append(s.queue, fn)
	s.mu.Unlock()
}

// Drain 跑完队列里的全部工作，返回跑了几件。
func (s *SyncRunner) Drain() int {
	n := 0
	for {
		s.mu.Lock()
		if len(s.queue) == 0 {
			s.mu.Unlock()
			return n
		}
		fn := s.queue[0]
		s.queue = s.queue[1:]
		s.mu.Unlock()
		fn()
		n++
	}
}
