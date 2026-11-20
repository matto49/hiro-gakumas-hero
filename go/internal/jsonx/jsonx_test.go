package jsonx

import (
	"testing"
)

func TestDecodeKeepsKeyOrderAndNumberText(t *testing.T) {
	in := `{"z":1,"a":{"y":1.0,"b":[2,"三",null,true]},"m":"<&>"}`
	v, err := Decode([]byte(in))
	if err != nil {
		t.Fatal(err)
	}
	out, err := Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != `{"z":1,"a":{"y":1.0,"b":[2,"三",null,true]},"m":"<&>"}` {
		t.Fatalf("got %s", out)
	}
}

func TestObjectGetSetDelete(t *testing.T) {
	o := NewObject()
	o.Set("b", 1)
	o.Set("a", "x")
	o.Set("b", 2) // 改值不改位置
	if got, _ := Marshal(o); string(got) != `{"b":2,"a":"x"}` {
		t.Fatalf("got %s", got)
	}
	if v, ok := o.Get("a"); !ok || v != "x" {
		t.Fatalf("Get a = %v %v", v, ok)
	}
	if _, ok := o.Get("nope"); ok {
		t.Fatal("Get nope should be missing")
	}
	o.Delete("b")
	if got, _ := Marshal(o); string(got) != `{"a":"x"}` {
		t.Fatalf("after delete got %s", got)
	}
	if o.Len() != 1 || o.Keys()[0] != "a" {
		t.Fatalf("keys %v", o.Keys())
	}
}

func TestMarshalStructDoesNotEscapeHTML(t *testing.T) {
	type s struct {
		A string `json:"a"`
	}
	got, err := Marshal(s{A: "<b>&</b> 中文"})
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != `{"a":"<b>&</b> 中文"}` {
		t.Fatalf("got %s", got)
	}
}

func TestDecodeRejectsTrailingData(t *testing.T) {
	if _, err := Decode([]byte(`{"a":1} {"b":2}`)); err == nil {
		t.Fatal("want error for trailing data")
	}
}

func TestHelpers(t *testing.T) {
	v, _ := Decode([]byte(`{"s":"x","n":3,"f":2.5,"o":{"k":1},"l":[1],"z":null}`))
	o := v.(*Object)
	if Str(o, "s") != "x" || Str(o, "n") != "" || Str(o, "missing") != "" {
		t.Fatal("Str")
	}
	if n, ok := Int(o, "n"); !ok || n != 3 {
		t.Fatal("Int n")
	}
	if _, ok := Int(o, "f"); ok {
		t.Fatal("Int f should fail (not an integer)")
	}
	if Obj(o, "o") == nil || Obj(o, "s") != nil {
		t.Fatal("Obj")
	}
	if len(List(o, "l")) != 1 || List(o, "s") != nil {
		t.Fatal("List")
	}
	if !IsNull(o, "z") || IsNull(o, "s") || IsNull(o, "missing") {
		t.Fatal("IsNull")
	}
}
