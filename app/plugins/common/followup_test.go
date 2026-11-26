package common_test

import (
	"testing"

	"github.com/egoist/mygo/ui"

	"github.com/matto49/hiro-gakumas-hero/app/hiroapi"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/common"
	"github.com/matto49/hiro-gakumas-hero/app/plugins/plugintest"
)

// #121 review：查中文译名失败时不能把空译文缓存下来，否则之后一直显示日文、不再重试。
func TestTextsRetriesAfterFailure(t *testing.T) {
	h := plugintest.New(nil) // 没有 catalog lookup 的回应：Call 返回失败
	c := &common.Catalog{}
	ja := []string{"パラメータ+10"}
	if got := c.Texts(h, ja); got[0] != "パラメータ+10" {
		t.Fatalf("失败时应保留原文：%v", got)
	}
	h.Resp["catalog lookup"] = hiroapi.Result{OK: true, Data: []byte(`{"texts":{"パラメータ+10":"参数+10"}}`)}
	c.Texts(h, ja) // 上次失败没缓存：这次重新查
	if got := c.Texts(h, ja); got[0] != "参数+10" {
		t.Fatalf("失败后没有重试：%v", got)
	}
}

// #121 review：读取偶像卡失败时显示原因和「重新读取偶像卡」，不误报「还没扫描过」；点了重新读取会再查。
func TestIdolPickerShowsLoadFailure(t *testing.T) {
	h := plugintest.New(nil) // info idols 失败
	p := &common.IdolPicker{}
	tt := ui.NewTester(plugintest.PanelView(func(c *ui.Context) { p.Draw(c, h, "t") }), 600, 800)
	tt.Frame()
	tt.Frame()
	if tt.HasText("还没扫描过偶像卡") || !tt.HasText("偶像卡列表没读出来") {
		t.Fatal("读取失败被当成了没扫描过")
	}
	h.Resp["info idols"] = hiroapi.Result{OK: true, Data: []byte(`{"items":[]}`)}
	if err := tt.Click("重新读取偶像卡"); err != nil {
		t.Fatal(err)
	}
	tt.Frame()
	tt.Frame()
	if !tt.HasText("还没扫描过偶像卡") {
		t.Fatal("重新读取后应显示真实状态（库存为空）")
	}
}
