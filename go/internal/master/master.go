// Package master 是主数据里的技能卡、饮料、P 道具（同 Python 版 hiro.master）：读 assets 的 game.db（KAA 公开主数据）。
// 整表按第一次用到时缓存；没有主数据时各函数返回空（Python 那边抛 CatalogUnavailable，识别层当「认不出」）。
// 模拟器主数据的退路（game.db 没有的新卡）阶段 4 随决策层一起迁。
package master

import (
	"database/sql"
	"errors"
	"sync"

	"github.com/matto49/hiro-gakumas-hero/go/internal/assets"
)

// ErrUnavailable 是没有主数据。
var ErrUnavailable = errors.New("没有主数据（game.db）")

// Card 是技能卡的一个强化版本（ProduceCard 一行）。
type Card struct {
	ID                   string
	UpgradeCount         int
	Name                 string
	Rarity               string
	PlanType             string
	Category             string
	Stamina              *int
	ForceStamina         *int
	CostType             string
	CostValue            *int
	PlayMovePositionType string
	ProduceDescriptions  string // JSON 文本
	PlayEffects          string // JSON 文本
	AssetID              string
}

// Drink 是一种 P 饮料。
type Drink struct {
	ID, AssetID, Name string
}

type tables struct {
	once   sync.Once
	cards  []Card
	drinks []Drink
	pitems []Drink // P 道具：同样只要 id / 资源名 / 名称
	err    error
}

var (
	mu  sync.Mutex
	cur *tables
)

func load() *tables {
	mu.Lock()
	if cur == nil {
		cur = &tables{}
	}
	t := cur
	mu.Unlock()
	t.once.Do(func() {
		db := assets.Default().MasterDB()
		if db == nil {
			t.err = ErrUnavailable
			return
		}
		if t.cards, t.err = queryCards(db); t.err != nil {
			return
		}
		if t.drinks, t.err = queryDrinks(db, "ProduceDrink"); t.err != nil {
			return
		}
		t.pitems, t.err = queryDrinks(db, "ProduceItem")
	})
	return t
}

// Clear 换了主数据（测试）后清缓存。
func Clear() {
	mu.Lock()
	cur = nil
	mu.Unlock()
}

func str(v sql.NullString) string { return v.String }

func intp(v sql.NullInt64) *int {
	if !v.Valid {
		return nil
	}
	n := int(v.Int64)
	return &n
}

func queryCards(db *sql.DB) ([]Card, error) {
	rows, err := db.Query(`SELECT id, upgradeCount, name, rarity, planType, category, stamina, forceStamina, costType, costValue,
		playMovePositionType, produceDescriptions, playEffects, assetId FROM ProduceCard`)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []Card
	for rows.Next() {
		var (
			c                                                    Card
			up, stamina, force, cost                             sql.NullInt64
			name, rarity, plan, cat, ctype, move, desc, eff, ast sql.NullString
		)
		if err := rows.Scan(&c.ID, &up, &name, &rarity, &plan, &cat, &stamina, &force, &ctype, &cost, &move, &desc, &eff, &ast); err != nil {
			return nil, err
		}
		c.UpgradeCount = int(up.Int64)
		c.Name, c.Rarity, c.PlanType, c.Category, c.CostType = str(name), str(rarity), str(plan), str(cat), str(ctype)
		c.PlayMovePositionType, c.ProduceDescriptions, c.PlayEffects, c.AssetID = str(move), str(desc), str(eff), str(ast)
		c.Stamina, c.ForceStamina, c.CostValue = intp(stamina), intp(force), intp(cost)
		out = append(out, c)
	}
	return out, rows.Err()
}

func queryDrinks(db *sql.DB, table string) ([]Drink, error) {
	rows, err := db.Query("SELECT id, assetId, name FROM " + table) //nolint:gosec // 表名是常量
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []Drink
	for rows.Next() {
		var d Drink
		var asset, name sql.NullString
		if err := rows.Scan(&d.ID, &asset, &name); err != nil {
			return nil, err
		}
		d.AssetID, d.Name = asset.String, name.String
		out = append(out, d)
	}
	return out, rows.Err()
}

// Cards 全部技能卡（每个强化版本一项，表里的顺序）。
func Cards() []Card { return load().cards }

// Drinks 全部 P 饮料。
func Drinks() []Drink { return load().drinks }

// PItems 全部 P 道具（id、资源名、名称）。
func PItems() []Drink { return load().pitems }

// Available 有没有主数据。
func Available() bool { return load().err == nil }

// CardOf 按 (卡 id, 强化次数) 取；没有返回 nil。
func CardOf(id string, upgrade int) *Card {
	for i, c := range load().cards {
		if c.ID == id && c.UpgradeCount == upgrade {
			return &load().cards[i]
		}
	}
	return nil
}

// CardByAsset 卡图资源名 → 卡；同一张图有多个强化版本时取第一行（同 KAA SkillCard.from_asset_id）。
func CardByAsset(asset string) *Card {
	for i, c := range load().cards {
		if c.AssetID == asset {
			return &load().cards[i]
		}
	}
	return nil
}

// DrinkByAsset 饮料图资源名 → 饮料。
func DrinkByAsset(asset string) *Drink {
	for i, d := range load().drinks {
		if d.AssetID == asset {
			return &load().drinks[i]
		}
	}
	return nil
}
