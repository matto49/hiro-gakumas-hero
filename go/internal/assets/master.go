package assets

import (
	"database/sql"
	"fmt"
	"path/filepath"
	"sync"
)

// MasterDB KAA 公开数据的主数据库 game.db（只读）；没有文件或是空库（KAA 首次运行建的空文件）时返回 nil。
func (s *Source) MasterDB() *sql.DB {
	s.dbOnce.Do(func() {
		p, err := filepath.Abs(filepath.Join(s.KAA, "game.db"))
		if err != nil || !isFile(p) {
			return
		}
		db, err := sql.Open("sqlite", "file:"+p+"?mode=ro")
		if err != nil {
			return
		}
		var n int
		if db.QueryRow("select count(*) from sqlite_master where type='table' and name='ProduceCard'").Scan(&n) != nil || n == 0 {
			_ = db.Close()
			return
		}
		s.db = db
	})
	return s.db
}

// Card 是一张技能卡（某个强化次数）。
type Card struct {
	ID       string `json:"id"`
	Upgrade  int    `json:"upgrade"`
	Name     string `json:"name"`
	Rarity   string `json:"rarity"`
	Plan     string `json:"plan"`
	Category string `json:"category"`
	CostType string `json:"cost_type"`
	Cost     int    `json:"cost"`
	Asset    string `json:"asset"`
	Initial  bool   `json:"initial"`
}

// Key 卡 id 加强化次数后缀（"<id>@<n>"），同 Python card_catalog 的键。
func (c Card) Key() string { return fmt.Sprintf("%s@%d", c.ID, c.Upgrade) }

// Item 是一种 P 饮料或 P 道具。
type Item struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Rarity string `json:"rarity"`
	Plan   string `json:"plan"`
	Asset  string `json:"asset"`
}

type catalogs struct {
	once   sync.Once
	cards  map[string]Card
	drinks map[string]Item
	pitems map[string]Item
	err    error
}

var masterCache sync.Map // *Source → *catalogs

func (s *Source) catalogs() *catalogs {
	v, _ := masterCache.LoadOrStore(s, &catalogs{})
	c, _ := v.(*catalogs)
	c.once.Do(func() {
		db := s.MasterDB()
		if db == nil {
			return
		}
		c.cards, c.err = queryCards(db)
		if c.err == nil {
			c.drinks, c.err = queryItems(db, "ProduceDrink")
		}
		if c.err == nil {
			c.pitems, c.err = queryItems(db, "ProduceItem")
		}
	})
	return c
}

func queryCards(db *sql.DB) (map[string]Card, error) {
	rows, err := db.Query("select id, upgradeCount, name, rarity, planType, category, costType, costValue, assetId, isInitial from ProduceCard")
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	out := map[string]Card{}
	for rows.Next() {
		var c Card
		if err := rows.Scan(&c.ID, &c.Upgrade, &c.Name, &c.Rarity, &c.Plan, &c.Category, &c.CostType, &c.Cost, &c.Asset, &c.Initial); err != nil {
			return nil, err
		}
		out[c.Key()] = c
	}
	return out, rows.Err()
}

func queryItems(db *sql.DB, table string) (map[string]Item, error) {
	rows, err := db.Query("select id, name, rarity, planType, assetId from " + table) //nolint:gosec // 表名是常量
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	out := map[string]Item{}
	for rows.Next() {
		var it Item
		if err := rows.Scan(&it.ID, &it.Name, &it.Rarity, &it.Plan, &it.Asset); err != nil {
			return nil, err
		}
		out[it.ID] = it
	}
	return out, rows.Err()
}

// CardCatalog 卡 id@强化次数 → 卡；没有主数据返回 nil。
func (s *Source) CardCatalog() map[string]Card { return s.catalogs().cards }

// DrinkCatalog P 饮料 id → 饮料；没有主数据返回 nil。
func (s *Source) DrinkCatalog() map[string]Item { return s.catalogs().drinks }

// PItemCatalog P 道具 id → 道具；没有主数据返回 nil。
func (s *Source) PItemCatalog() map[string]Item { return s.catalogs().pitems }
