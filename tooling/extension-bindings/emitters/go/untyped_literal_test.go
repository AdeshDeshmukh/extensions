package goemitter

import (
	"encoding/json"
	"strings"
	"testing"
)

// Values with no Go type, such as items of an array schema that only has
// "contains", are written as any literals so README examples still compile.
func TestUntypedLiteral(t *testing.T) {
	var value any
	decoder := json.NewDecoder(strings.NewReader(`[{"property": "measurementId", "name": "GA_ID", "sensitive": false, "size": 1.5, "note": null}]`))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		t.Fatal(err)
	}
	got, err := goLiteral("any", value)
	if err != nil {
		t.Fatal(err)
	}
	want := `[]any{map[string]any{"name": "GA_ID", "note": nil, "property": "measurementId", "sensitive": false, "size": 1.5}}`
	if got != want {
		t.Fatalf("got %s\nwant %s", got, want)
	}
}
