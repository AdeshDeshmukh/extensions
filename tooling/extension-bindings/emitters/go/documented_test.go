package goemitter

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

const documentedFixture = "documented-common-integrations"
const documentedRoot = "https://runtimeconditions.io/go-fixture/documented-common-integrations:1.0.0"

// TestDocumentedExtensionEmitsGodoc checks the whole documentation chain:
// descriptions in the extension YAML (kinds, interface types and JSON Schema
// properties) reach the binding model and become Godoc in the generated code.
// Set RC_UPDATE_GOLDEN=1 to rewrite the golden file after an intended change.
func TestDocumentedExtensionEmitsGodoc(t *testing.T) {
	fixture := filepath.Join("testdata", documentedFixture)
	source := emitDocumented(t, fixture)

	golden := filepath.Join(fixture, "bindings.go.golden")
	if os.Getenv("RC_UPDATE_GOLDEN") == "1" {
		if err := os.WriteFile(golden, source, 0644); err != nil {
			t.Fatal(err)
		}
	}
	expected, err := os.ReadFile(golden)
	if err != nil {
		t.Fatal(err)
	}
	if string(source) != string(expected) {
		t.Fatalf("generated bindings differ from %s; rerun with RC_UPDATE_GOLDEN=1 if the change is intended", golden)
	}

	for _, want := range []string{
		// Kind description on the declaration function.
		"// The workload calls another service through a network API",
		// Interface type description on the interface struct.
		"// Http: The API is reached over HTTP.",
		// JSON Schema property descriptions on struct fields.
		"\t// Where the API document can be found",
		"\t// HTTP method of the operation, such as GET or POST.",
		"\t// The cache engine the workload needs, redis or memcached.",
	} {
		if !strings.Contains(string(source), want) {
			t.Errorf("generated bindings are missing doc comment %q", want)
		}
	}
}

// TestDescriptionsDoNotChangeTheAPI emits the same extension with every
// optional description removed. The exported API must be identical, so adding
// documentation is never a breaking change for release planning.
func TestDescriptionsDoNotChangeTheAPI(t *testing.T) {
	documented := filepath.Join("testdata", documentedFixture)
	plain := filepath.Join(t.TempDir(), "plain")
	if err := os.MkdirAll(plain, 0755); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(documented, "root.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var extension map[string]any
	if err = yaml.Unmarshal(data, &extension); err != nil {
		t.Fatal(err)
	}
	stripOptionalDescriptions(extension)
	stripped, err := yaml.Marshal(extension)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(stripped), "description:") != 5 {
		t.Fatalf("expected only the five required schema descriptions to remain")
	}
	if err = os.WriteFile(filepath.Join(plain, "root.yaml"), stripped, 0644); err != nil {
		t.Fatal(err)
	}
	documentedAPI := apiOf(t, documented, emitDocumented(t, documented))
	plainAPI := apiOf(t, plain, emitDocumented(t, plain))
	if !reflect.DeepEqual(documentedAPI, plainAPI) {
		t.Fatalf("descriptions changed the exported API:\ndocumented: %v\nplain:      %v", documentedAPI, plainAPI)
	}
}

func emitDocumented(t *testing.T, catalog string) []byte {
	t.Helper()
	model := normalizeFixture(t, catalog, documentedRoot)
	target, err := loadFixtureTarget(t, filepath.Join("testdata", documentedFixture, "package-target.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(t.TempDir(), "generated")
	if err := Emit(model, target, output); err != nil {
		t.Fatal(err)
	}
	source, err := os.ReadFile(filepath.Join(output, generatedGoFile))
	if err != nil {
		t.Fatal(err)
	}
	return source
}

func apiOf(t *testing.T, directory string, source []byte) []string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "bindings.go")
	if err := os.WriteFile(path, source, 0644); err != nil {
		t.Fatal(err)
	}
	api, err := SourceAPISurface(path)
	if err != nil {
		t.Fatal(err)
	}
	return api
}

// stripOptionalDescriptions removes kind, interface type and JSON Schema
// descriptions, keeping the description that every schemas[] entry requires.
func stripOptionalDescriptions(extension map[string]any) {
	spec := extension["spec"].(map[string]any)
	for _, list := range []string{"kinds", "interfaceTypes"} {
		for _, item := range spec[list].([]any) {
			delete(item.(map[string]any), "description")
		}
	}
	for _, item := range spec["schemas"].([]any) {
		stripSchemaDescriptions(item.(map[string]any)["schema"])
	}
}

func stripSchemaDescriptions(value any) {
	switch typed := value.(type) {
	case map[string]any:
		delete(typed, "description")
		for _, child := range typed {
			stripSchemaDescriptions(child)
		}
	case []any:
		for _, child := range typed {
			stripSchemaDescriptions(child)
		}
	}
}
