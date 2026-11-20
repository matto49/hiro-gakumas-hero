// Package jsonx 是和 Python 版 hiro 对齐的 JSON 读写：
//
//   - 对象保留键的顺序（Python dict 保留插入顺序；输出顺序对模型有用，例如 counts 在 items 前面）。
//   - 数字保留原文（json.Number）：1.0 不会变成 1，大整数不丢精度。
//   - 输出不转义 <>&、保留中文原文，同 Python json.dumps(ensure_ascii=False)。
//
// Decode 得到的值只有这几种：nil、bool、string、json.Number、[]any、*Object。
package jsonx

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
)

// Object 是保序的 JSON 对象。零值不能用，用 NewObject。
type Object struct {
	keys []string
	vals map[string]any
}

// NewObject 建一个空对象。
func NewObject() *Object {
	return &Object{vals: map[string]any{}}
}

// Set 设一个键；已有的键改值、位置不变，新键排在最后。
func (o *Object) Set(k string, v any) *Object {
	if _, ok := o.vals[k]; !ok {
		o.keys = append(o.keys, k)
	}
	o.vals[k] = v
	return o
}

// Get 取一个键的值；ok=false 表示没有这个键（有这个键、值是 null 时 ok=true、v=nil）。
func (o *Object) Get(k string) (any, bool) {
	if o == nil {
		return nil, false
	}
	v, ok := o.vals[k]
	return v, ok
}

// Has 有没有这个键（值是 null 也算有，同 Python 的 `k in d`）。
func (o *Object) Has(k string) bool {
	_, ok := o.Get(k)
	return ok
}

// Delete 删一个键。
func (o *Object) Delete(k string) {
	if _, ok := o.vals[k]; !ok {
		return
	}
	delete(o.vals, k)
	for i, kk := range o.keys {
		if kk == k {
			o.keys = append(o.keys[:i], o.keys[i+1:]...)
			break
		}
	}
}

// Keys 按顺序返回全部键（副本）。
func (o *Object) Keys() []string {
	if o == nil {
		return nil
	}
	return append([]string(nil), o.keys...)
}

// Len 键的个数。
func (o *Object) Len() int {
	if o == nil {
		return 0
	}
	return len(o.keys)
}

// MarshalJSON 按键的顺序输出。
func (o *Object) MarshalJSON() ([]byte, error) {
	if o == nil {
		return []byte("null"), nil
	}
	var b bytes.Buffer
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, err := Marshal(k)
		if err != nil {
			return nil, err
		}
		b.Write(kb)
		b.WriteByte(':')
		vb, err := Marshal(o.vals[k])
		if err != nil {
			return nil, fmt.Errorf("键 %q：%w", k, err)
		}
		b.Write(vb)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}

// Marshal 输出紧凑 JSON，不转义 <>&，结尾不带换行。
func Marshal(v any) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(b.Bytes(), "\n"), nil
}

// Decode 解析一段 JSON（只能有一个值），对象保序、数字保留原文。
func Decode(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	v, err := decodeValue(dec)
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); !errors.Is(err, io.EOF) {
		return nil, errors.New("JSON 后面还有多余的内容")
	}
	return v, nil
}

// DecodeObject 解析一段 JSON，要求顶层是对象。
func DecodeObject(data []byte) (*Object, error) {
	v, err := Decode(data)
	if err != nil {
		return nil, err
	}
	o, ok := v.(*Object)
	if !ok {
		return nil, errors.New("JSON 顶层不是对象")
	}
	return o, nil
}

func decodeValue(dec *json.Decoder) (any, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			o := NewObject()
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return nil, err
				}
				k, ok := kt.(string)
				if !ok {
					return nil, fmt.Errorf("对象的键不是字符串：%v", kt)
				}
				v, err := decodeValue(dec)
				if err != nil {
					return nil, err
				}
				o.Set(k, v)
			}
			if _, err := dec.Token(); err != nil { // '}'
				return nil, err
			}
			return o, nil
		case '[':
			list := []any{}
			for dec.More() {
				v, err := decodeValue(dec)
				if err != nil {
					return nil, err
				}
				list = append(list, v)
			}
			if _, err := dec.Token(); err != nil { // ']'
				return nil, err
			}
			return list, nil
		}
		return nil, fmt.Errorf("意外的符号 %v", t)
	default:
		return tok, nil // nil、bool、string、json.Number
	}
}

// ---------------------------------------------------------------- 取值小工具（键不存在或类型不对时给零值）

// Str 取字符串；不是字符串（含 null、数字）给 ""。
func Str(o *Object, k string) string {
	v, _ := o.Get(k)
	s, _ := v.(string)
	return s
}

// Int 取整数；不是整数给 ok=false。
func Int(o *Object, k string) (int64, bool) {
	v, _ := o.Get(k)
	return AsInt(v)
}

// AsInt 把一个值当整数读（json.Number、Go 整数）。
func AsInt(v any) (int64, bool) {
	switch n := v.(type) {
	case json.Number:
		i, err := strconv.ParseInt(string(n), 10, 64)
		return i, err == nil
	case int:
		return int64(n), true
	case int64:
		return n, true
	}
	return 0, false
}

// Obj 取对象；不是对象给 nil。
func Obj(o *Object, k string) *Object {
	v, _ := o.Get(k)
	oo, _ := v.(*Object)
	return oo
}

// List 取数组；不是数组给 nil。
func List(o *Object, k string) []any {
	v, _ := o.Get(k)
	l, _ := v.([]any)
	return l
}

// IsNull 有这个键、值是 null。
func IsNull(o *Object, k string) bool {
	v, ok := o.Get(k)
	return ok && v == nil
}

// Truthy 同 Python 的真值：nil、false、""、0、空数组、空对象都是假。
func Truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case json.Number:
		f, err := t.Float64()
		return err != nil || f != 0
	case int:
		return t != 0
	case int64:
		return t != 0
	case float64:
		return t != 0
	case []any:
		return len(t) > 0
	case *Object:
		return t.Len() > 0
	}
	return true
}
