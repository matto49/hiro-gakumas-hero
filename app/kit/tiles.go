package kit

import (
	"fmt"

	"github.com/egoist/mygo/ui"
)

// Tile 是卡牌小块里的一格：小图（可空）、名字、名字下的小字（等级等）、行尾的标签（租借 / 回忆时机），Tip 是悬停说明。
type Tile struct {
	Thumb, Tag, Name, Note, Tip string
}

// Tiles 画一组卡牌小块：小标题，下面每张卡一块（图标 · 名字 / 小字 · 行尾 tag），按宽度两三块一行（11-25 review：
// 「租借在卡的最后打一个 tag，前面还是要图标；回忆也要图标，hover 展示详细信息；大卡片列下去太长了」）。
// 确认卡和对话里的编成方案卡共用。
func Tiles(c *ui.Context, key, title string, items []Tile) {
	ui.Column(c).Key(key).Gap(6).Children(func() {
		if title != "" {
			ui.Text(c, title).FontSize(11.5).FontWeight(600).TextColor(Ink2)
		}
		ui.Row(c).Wrap().Gap(6).Children(func() {
			for i, it := range items {
				c.Key(fmt.Sprintf("%s-%d", key, i))
				tile := ui.Row(c).Width(230).Grow(1).Shrink(1).Gap(8).AlignItems(ui.Center).Padding(5, 8, 5, 5).Radius(R8).Background(Surface2)
				if it.Tip != "" {
					tile = tile.Tooltip(it.Tip)
				}
				tile.Children(func() {
					if it.Thumb != "" {
						Thumb(c, it.Thumb, 30, 30, 6)
					} else {
						ui.Box(c).Size(30, 30).Radius(6).Background(Line).Shrink(0)
					}
					ui.Column(c).Gap(1).Grow(1).Shrink(1).Children(func() {
						ui.Text(c, it.Name).FontSize(FSmall).TextColor(Ink).MaxLines(1).Ellipsis("…")
						if it.Note != "" {
							ui.Text(c, it.Note).FontSize(11).TextColor(Ink2).SingleLine()
						}
					})
					if it.Tag != "" {
						ui.Text(c, it.Tag).FontSize(11).TextColor(Ink2).Padding(1, 6).Radius(5).Background(CardBg).Border(Hair, Line).Shrink(0)
					}
				})
			}
		})
	})
}
