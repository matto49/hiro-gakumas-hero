package service

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"

	"github.com/matto49/hiro-gakumas-hero/go/internal/clierr"
	"github.com/matto49/hiro-gakumas-hero/go/internal/home"
	"github.com/matto49/hiro-gakumas-hero/go/internal/jsonx"
	"github.com/matto49/hiro-gakumas-hero/go/internal/proc"
)

const spawnTimeout = 15 * time.Second

// SpawnArgv 是拉起服务的命令（缺省 hiro 自己 + 隐藏子命令；测试里换）。
var SpawnArgv = func() ([]string, error) {
	exe, err := os.Executable()
	if err != nil {
		return nil, err
	}
	return []string{exe, ServiceCommand}, nil
}

func readInfo(p string) (*Info, error) {
	data, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	var info Info
	if err := json.Unmarshal(data, &info); err != nil {
		return nil, err
	}
	if info.Port == 0 || info.Token == "" {
		return nil, fmt.Errorf("service.json 不完整")
	}
	return &info, nil
}

func currentInfo() (*Info, error) {
	h, err := home.Ensure()
	if err != nil {
		return nil, err
	}
	return readInfo(filepath.Join(h, "service.json"))
}

func post(info *Info, path string, body any, timeout time.Duration) (*jsonx.Object, error) {
	if body == nil {
		body = jsonx.NewObject()
	}
	data, err := jsonx.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequest(http.MethodPost, "http://127.0.0.1:"+strconv.Itoa(info.Port)+"/"+path, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Hiro-Token", info.Token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: timeout}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err // 服务正在退出时响应会被截断
	}
	return jsonx.DecodeObject(raw)
}

func alive(info *Info) bool {
	if info == nil {
		return false
	}
	env, err := post(info, "ping", nil, 5*time.Second)
	if err != nil {
		return false
	}
	ok, _ := env.Get("ok")
	return ok == true
}

// spawn 拉起服务。拿着 spawn.lock 做：几个客户端同时发现服务没在跑时只拉起一个；服务锁被拿着（服务在跑、只是慢）就等它。
func spawn() (*Info, error) {
	h, err := home.Ensure()
	if err != nil {
		return nil, err
	}
	lock := NewFileLock(filepath.Join(h, "spawn.lock"))
	ok, err := lock.Acquire(true, 30*time.Second)
	if err != nil || !ok {
		return nil, clierr.New("service_down", "等不到拉起服务的锁", "再试一次；一直这样运行 hiro doctor")
	}
	defer lock.Release()
	if info, _ := currentInfo(); alive(info) {
		return info, nil
	}
	if !HeldElsewhere(filepath.Join(h, "service.lock")) {
		_ = os.Remove(filepath.Join(h, "service.json"))
		if err := startProcess(h); err != nil {
			return nil, clierr.New("service_down", "hiro 服务没能启动："+err.Error(), "看日志："+filepath.Join(h, "service.log"))
		}
	}
	deadline := time.Now().Add(spawnTimeout)
	for time.Now().Before(deadline) {
		if info, _ := currentInfo(); alive(info) {
			return info, nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	return nil, clierr.New("service_down", "hiro 服务没能启动", "看日志："+filepath.Join(h, "service.log"))
}

func startProcess(h string) error {
	argv, err := SpawnArgv()
	if err != nil {
		return err
	}
	log, err := os.OpenFile(filepath.Join(h, "service.log"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer log.Close()
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = h
	cmd.Stdout, cmd.Stderr = log, log
	proc.Detach(cmd)
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

// Call 调服务接口，返回信封；服务没在跑就先拉起。
func Call(path string, body any) (*jsonx.Object, error) {
	info, _ := currentInfo()
	if !alive(info) {
		var err error
		if info, err = spawn(); err != nil {
			return nil, err
		}
	}
	env, err := post(info, path, body, 30*time.Second)
	if err != nil {
		return nil, clierr.New("service_down", "连不上 hiro 服务："+err.Error(), "再试一次；一直这样运行 hiro doctor")
	}
	return env, nil
}

// Data 调服务接口，失败转成 *clierr.Error（bad_args 退出码 2）。
func Data(path string, body any) (any, error) {
	env, err := Call(path, body)
	if err != nil {
		return nil, err
	}
	if ok, _ := env.Get("ok"); ok == true {
		v, _ := env.Get("data")
		return v, nil
	}
	e := jsonx.Obj(env, "error")
	code := jsonx.Str(e, "code")
	if code == "" {
		code = "error"
	}
	msg := jsonx.Str(e, "message")
	if msg == "" {
		msg = "服务出错"
	}
	exit := 1
	if code == "bad_args" {
		exit = 2
	}
	return nil, &clierr.Error{Code: code, Message: msg, Hint: jsonx.Str(e, "hint"), Exit: exit}
}

// Shutdown 让服务退出（会先停掉正在跑的任务），等它不再响应。
func Shutdown() {
	info, _ := currentInfo()
	if !alive(info) {
		return
	}
	_, _ = post(info, "shutdown", nil, 15*time.Second)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) && alive(info) {
		time.Sleep(100 * time.Millisecond)
	}
}
