package extplugin

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
	"time"
)

// IdleTimeout 是插件进程多久没请求就关掉（下次用时再起）。
const IdleTimeout = 60 * time.Second

// DrainGrace 是插件进程退出后，最多再等多久把它写在管道里的回复读完。
var DrainGrace = 2 * time.Second

// CallTimeout 是一次请求最多等多久（包括往插件 stdin 写）。测试里可以改短。
var CallTimeout = 15 * time.Second

type rpcMsg struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      any             `json:"id,omitempty"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

func (e *rpcError) Error() string { return e.Message }

// Handler 处理插件发给宿主的请求（hiro.call）。
type Handler func(ctx context.Context, method string, params json.RawMessage) (any, error)

// client 是一个插件子进程的 JSON-RPC 连接：按需启动，空闲回收，进程退出时让在等的请求都失败。
type client struct {
	dir  string
	argv []string
	env  []string
	on   Handler

	mu      sync.Mutex
	cmd     *exec.Cmd
	stdin   io.WriteCloser
	wmu     sync.Mutex
	waiting map[int64]chan rpcMsg
	next    int64
	last    time.Time
	gen     int // 每起一次进程加一
	closed  bool
}

func newClient(dir string, argv, env []string, on Handler) *client {
	c := &client{dir: dir, argv: argv, env: env, on: on, waiting: map[int64]chan rpcMsg{}}
	go c.reaper()
	return c
}

func (c *client) startLocked() error {
	if c.cmd != nil {
		return nil
	}
	if c.closed {
		return errors.New("插件已关闭")
	}
	cmd := exec.Command(c.argv[0], c.argv[1:]...)
	cmd.Dir = c.dir
	cmd.Env = c.env
	cmd.Stderr = os.Stderr
	hide(cmd)
	in, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	// stdout 用自己开的管道（*os.File）：Wait 只等父进程退出，不等占着管道的子孙进程，也不会替我们关读端
	out, w, err := os.Pipe()
	if err != nil {
		return err
	}
	cmd.Stdout = w
	if err := cmd.Start(); err != nil {
		out.Close()
		w.Close()
		return fmt.Errorf("插件起不来：%w", err)
	}
	w.Close()
	c.cmd, c.stdin = cmd, in
	c.gen++
	gen := c.gen
	readDone := make(chan struct{})
	go func() {
		c.read(out, gen)
		close(readDone)
	}()
	go func() {
		_ = cmd.Wait() // 父进程一退出就返回
		// 它退出前写的回复先读完；子孙进程占着管道时最多等 DrainGrace，然后不再读
		select {
		case <-readDone:
		case <-time.After(DrainGrace):
		}
		out.Close()
		c.mu.Lock()
		if c.gen == gen {
			c.cmd, c.stdin = nil, nil
			for id, ch := range c.waiting {
				ch <- rpcMsg{Error: &rpcError{Code: -32000, Message: "插件进程退出了"}}
				delete(c.waiting, id)
			}
		}
		c.mu.Unlock()
	}()
	return nil
}

func (c *client) write(m rpcMsg) error {
	m.JSONRPC = "2.0"
	b, err := json.Marshal(m)
	if err != nil {
		return err
	}
	c.mu.Lock()
	w := c.stdin
	c.mu.Unlock()
	if w == nil {
		return errors.New("插件没在运行")
	}
	c.wmu.Lock()
	defer c.wmu.Unlock()
	_, err = w.Write(append(b, '\n'))
	return err
}

func (c *client) read(r io.Reader, gen int) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64*1024), 8<<20)
	for sc.Scan() {
		var m rpcMsg
		if json.Unmarshal(sc.Bytes(), &m) != nil {
			continue
		}
		if m.Method != "" {
			go c.serve(m)
			continue
		}
		id, ok := toInt(m.ID)
		if !ok {
			continue
		}
		c.mu.Lock()
		ch := c.waiting[id]
		delete(c.waiting, id)
		c.mu.Unlock()
		if ch != nil {
			ch <- m
		}
	}
}

func toInt(v any) (int64, bool) {
	switch x := v.(type) {
	case float64:
		return int64(x), true
	case json.Number:
		n, err := x.Int64()
		return n, err == nil
	}
	return 0, false
}

func (c *client) serve(m rpcMsg) {
	ctx, cancel := context.WithTimeout(context.Background(), CallTimeout)
	defer cancel()
	res, err := c.on(ctx, m.Method, m.Params)
	reply := rpcMsg{ID: m.ID}
	if err != nil {
		reply.Error = &rpcError{Code: -32000, Message: err.Error()}
	} else {
		reply.Result, _ = json.Marshal(res)
	}
	_ = c.write(reply)
}

// Call 发一个请求并等回复，结果解到 out。
func (c *client) Call(ctx context.Context, method string, params, out any) error {
	ctx, cancel := context.WithTimeout(ctx, CallTimeout)
	defer cancel()
	p, err := json.Marshal(params)
	if err != nil {
		return err
	}
	c.mu.Lock()
	if err := c.startLocked(); err != nil {
		c.mu.Unlock()
		return err
	}
	c.next++
	id := c.next
	myGen := c.gen
	ch := make(chan rpcMsg, 1)
	c.waiting[id] = ch
	c.last = time.Now()
	c.mu.Unlock()
	drop := func() {
		c.mu.Lock()
		delete(c.waiting, id)
		c.mu.Unlock()
	}
	// 写也要受超时管：插件不读 stdin 时写会卡住，超时就把这个进程退掉（下次重起一个干净的）
	wrote := make(chan error, 1)
	go func() { wrote <- c.write(rpcMsg{ID: id, Method: method, Params: p}) }()
	select {
	case err := <-wrote:
		if err != nil {
			drop()
			return err
		}
	case <-ctx.Done():
		drop()
		c.mu.Lock()
		c.killGenLocked(myGen) // 只杀这次写的那一代；中间换过进程就不动新的
		c.mu.Unlock()
		return fmt.Errorf("插件 %s 没在 %v 内收下请求", method, CallTimeout)
	}
	select {
	case m := <-ch:
		c.mu.Lock()
		c.last = time.Now()
		c.mu.Unlock()
		if m.Error != nil {
			return m.Error
		}
		if out != nil && len(m.Result) > 0 {
			return json.Unmarshal(m.Result, out)
		}
		return nil
	case <-ctx.Done():
		drop()
		return fmt.Errorf("插件 %s 没在 %v 内回应", method, CallTimeout)
	}
}

func (c *client) reaper() {
	for {
		time.Sleep(IdleTimeout / 4)
		c.mu.Lock()
		if c.closed {
			c.mu.Unlock()
			return
		}
		if c.cmd != nil && len(c.waiting) == 0 && time.Since(c.last) > IdleTimeout {
			c.killLocked()
		}
		c.mu.Unlock()
	}
}

func (c *client) killLocked() {
	if c.cmd != nil && c.cmd.Process != nil {
		_ = c.cmd.Process.Kill()
	}
	if c.stdin != nil {
		_ = c.stdin.Close()
	}
	c.cmd, c.stdin = nil, nil
}

// killGenLocked 只在当前还是第 gen 代进程时杀掉它。
func (c *client) killGenLocked(gen int) {
	if c.gen == gen {
		c.killLocked()
	}
}

// Close 关掉插件进程，不再起。
func (c *client) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closed = true
	c.killLocked()
}
