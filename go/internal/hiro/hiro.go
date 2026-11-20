// Package hiro 把全部命令接到一起：hiro 命令行和桌面程序用同一个 Registry。登记顺序同 Python 版
// （内置 → info → catalog → run → secret → doctor → plan），`hiro tools` 的输出顺序也一样。
package hiro

import (
	"github.com/matto49/hiro-gakumas-hero/go/internal/catalog"
	"github.com/matto49/hiro-gakumas-hero/go/internal/cli"
	"github.com/matto49/hiro-gakumas-hero/go/internal/doctor"
	"github.com/matto49/hiro-gakumas-hero/go/internal/info"
	"github.com/matto49/hiro-gakumas-hero/go/internal/plan"
	"github.com/matto49/hiro-gakumas-hero/go/internal/run"
	"github.com/matto49/hiro-gakumas-hero/go/internal/secret"
)

// Registry 返回登记了全部命令的 Registry。
func Registry() *cli.Registry {
	r := cli.New()
	info.Register(r)
	catalog.Register(r)
	run.Register(r)
	secret.Register(r)
	doctor.Register(r)
	plan.Register(r)
	return r
}
