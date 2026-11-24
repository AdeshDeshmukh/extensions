package goemitter

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"

	"github.com/runtimeconditions/extensions/tooling/extension-bindings/normalizer"
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
		// Extension description as the package comment.
		"// Package common declares Runtime Conditions defined by",
		"// Common integrations a workload depends on",
		// Kind description on the declaration function.
		"// The workload calls another service through a network API",
		// Interface type description on the interface struct.
		"// Http: The API is reached over HTTP.",
		// Interface field description (meaning) followed by the JSON Schema
		// description (validation rule) on the struct field.
		"\t// The cache engine the workload needs.\n\t//\n\t// Optional. When present, must be one of the defined engine values.",
		// JSON Schema description of a nested property.
		"\t// Where the API document can be found",
		// Field value descriptions on the generated constants.
		"\t// Redis, an in-memory key/value store.\n\tKeyValueEngineRedis KeyValueEngine = \"redis\"",
		"\t// Read a resource.\n\tMethodGet Method = \"GET\"",
	} {
		if !strings.Contains(string(source), want) {
			t.Errorf("generated bindings are missing doc comment %q", want)
		}
	}
}

// TestUnknownValueDescriptionIsRejected checks that a valueDescriptions key
// must name one of the values it documents.
func TestUnknownValueDescriptionIsRejected(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("testdata", documentedFixture, "root.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	broken := strings.Replace(string(data), "redis: Redis, an in-memory key/value store.", "valkey: Not a defined value.", 1)
	if broken == string(data) {
		t.Fatal("fixture text changed; update this test")
	}
	catalog := t.TempDir()
	if err = os.WriteFile(filepath.Join(catalog, "root.yaml"), []byte(broken), 0644); err != nil {
		t.Fatal(err)
	}
	_, err = tryNormalize(catalog, documentedRoot)
	if err == nil || !strings.Contains(err.Error(), "RCB1315") || !strings.Contains(err.Error(), "valkey") {
		t.Fatalf("expected RCB1315 for the unknown key valkey, got %v", err)
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
	delete(extension["metadata"].(map[string]any), "description")
	spec := extension["spec"].(map[string]any)
	for _, list := range []string{"kinds", "interfaceTypes", "conditionFields", "interfaceFields"} {
		items, _ := spec[list].([]any)
		for _, item := range items {
			delete(item.(map[string]any), "description")
		}
	}
	for _, item := range spec["fieldValues"].([]any) {
		delete(item.(map[string]any), "valueDescriptions")
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

func tryNormalize(root, rootID string) (normalizer.BindingModel, error) {
	schemas, err := normalizer.LoadSchemas(
		filepath.Join("..", "..", "model", "runtimeconditions.extension-semantic.schema.yaml"),
		filepath.Join("..", "..", "model", "runtimeconditions.binding-model.schema.yaml"),
	)
	if err != nil {
		return normalizer.BindingModel{}, err
	}
	resolver, err := normalizer.NewResolver(normalizer.ResolverConfig{Schemas: schemas, CatalogRoots: []string{root}})
	if err != nil {
		return normalizer.BindingModel{}, err
	}
	closure, err := resolver.Resolve(context.Background(), rootID)
	if err != nil {
		return normalizer.BindingModel{}, err
	}
	return normalizer.Normalize(closure, normalizer.BuildDependencyLock(closure), schemas, normalizer.NormalizeConfig{
		CoreProfileSchema: normalizer.CoreProfileIdentity{
			ID: "https://runtimeconditions.io/test/core-profile-schema:1.0.0", Version: "0.0.0-test", SemanticSHA256: strings.Repeat("c", 64),
		},
		Normalizer: normalizer.ToolIdentity{
			Name: normalizer.NormalizerName, Version: normalizer.NormalizerVersion, SHA256: strings.Repeat("d", 64),
		},
	})
}

// TestExamplesCompile builds every example Condition of the documented fixture,
// as rendered in Godoc and the README, against the generated package.
func TestExamplesCompile(t *testing.T) {
	fixture := filepath.Join("testdata", documentedFixture)
	model := normalizeFixture(t, fixture, documentedRoot)
	target, err := loadFixtureTarget(t, filepath.Join(fixture, "package-target.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	workspace := t.TempDir()
	generated := filepath.Join(workspace, "generated")
	if err := Emit(model, target, generated); err != nil {
		t.Fatal(err)
	}
	ir, err := buildPackageIR(model, target)
	if err != nil {
		t.Fatal(err)
	}
	var statements []string
	for _, declaration := range model.Vocabulary.OwnedDeclarations {
		segments, err := normalizer.SplitDocumentation(declaration.Description)
		if err != nil {
			t.Fatal(err)
		}
		for _, segment := range segments {
			if segment.Condition == nil {
				continue
			}
			code, ok := ir.exampleCode(segment.Condition)
			if !ok {
				t.Fatalf("example for %s could not be rendered as Go:\n%s", declaration.Kind, segment.Source)
			}
			statements = append(statements, "_ = "+code)
		}
	}
	if len(statements) < 4 {
		t.Fatalf("expected the fixture's examples, found %d", len(statements))
	}
	consumer := filepath.Join(workspace, "consumer")
	if err := os.MkdirAll(consumer, 0o755); err != nil {
		t.Fatal(err)
	}
	goMod := "module example.com/consumer\n\ngo 1.22\n\nrequire " + target.ModulePath + " " + target.Version +
		"\n\nreplace " + target.ModulePath + " => " + generated + "\n"
	main := "package main\n\nimport " + target.PackageName + " " + strconv.Quote(target.ModulePath) +
		"\n\nfunc main() {\n" + strings.Join(statements, "\n") + "\n}\n"
	if err := os.WriteFile(filepath.Join(consumer, "go.mod"), []byte(goMod), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(consumer, "main.go"), []byte(main), 0o644); err != nil {
		t.Fatal(err)
	}
	command := exec.Command("go", "build", "./...")
	command.Dir = consumer
	command.Env = append(os.Environ(), "GOFLAGS=-mod=mod", "GOWORK=off")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("examples do not compile: %v\n%s\n%s", err, output, main)
	}

	readme, err := os.ReadFile(filepath.Join(generated, "README.md"))
	if err != nil {
		t.Fatalf("documented package has no README: %v", err)
	}
	for _, want := range []string{
		"# " + target.ModulePath,
		"go get " + target.ModulePath + "@" + target.Version,
		"```go\ncommon.Cache(",
		"In a Profile, this declares:\n\n```yaml\nkind: cache",
	} {
		if !strings.Contains(string(readme), want) {
			t.Errorf("README is missing %q", want)
		}
	}
}

// A package that adds fields to a dependency's kinds renders its examples as
// calls of the dependency's declaration function, with names that match the
// dependency's own package, and the code compiles against both packages.
func TestDependentExamplesCompile(t *testing.T) {
	const caseName = "15-documented-dependent"
	workspace := t.TempDir()
	modules := generateConformanceModules(t, caseName, workspace)
	dependency, root := modules[0], modules[len(modules)-1]

	derived, alias, ok := root.ir.dependencyIR(dependency.target.RootExtension)
	if !ok || alias != dependency.target.PackageName {
		t.Fatalf("dependency package could not be planned from the dependent model (alias %q)", alias)
	}
	if got, want := packageSymbols(derived), packageSymbols(dependency.ir); strings.Join(got, "\n") != strings.Join(want, "\n") {
		t.Fatalf("names planned from the dependent model differ from the dependency package:\ngot  %v\nwant %v", got, want)
	}

	var statements []string
	for _, field := range root.model.Vocabulary.ConditionFields {
		segments, err := normalizer.SplitDocumentation(field.Description)
		if err != nil {
			t.Fatal(err)
		}
		for _, segment := range segments {
			if segment.Condition == nil {
				continue
			}
			code, ok := root.ir.exampleCode(segment.Condition)
			if !ok {
				t.Fatalf("example for %s could not be rendered as Go:\n%s", field.Coordinate, segment.Source)
			}
			statements = append(statements, "_ = "+code)
		}
	}
	if len(statements) < 2 {
		t.Fatalf("expected the fixture's examples, found %d", len(statements))
	}
	consumer := filepath.Join(workspace, "consumer")
	if err := os.MkdirAll(consumer, 0o755); err != nil {
		t.Fatal(err)
	}
	goMod := "module example.com/consumer\n\ngo 1.22\n"
	main := "package main\n\nimport (\n\t" + dependency.target.PackageName + " " + strconv.Quote(dependency.target.ModulePath) +
		"\n\t" + root.target.PackageName + " " + strconv.Quote(root.target.ModulePath) +
		"\n)\n\nfunc main() {\n" + strings.Join(statements, "\n") + "\n}\n"
	if err := os.WriteFile(filepath.Join(consumer, "go.mod"), []byte(goMod), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(consumer, "main.go"), []byte(main), 0o644); err != nil {
		t.Fatal(err)
	}
	goWork := writeGoWork(t, workspace, modules, consumer)
	runCommand(t, consumer, goWork, "go", "build", "./...")

	readme, err := os.ReadFile(filepath.Join(root.path, "README.md"))
	if err != nil {
		t.Fatalf("documented dependent package has no README: %v", err)
	}
	for _, want := range []string{
		"## KeyValueConfiguration",
		"`documenteddependent.KeyValueConfiguration` sets the `configuration` field of `cache` Conditions with interface type `key_value`.",
		"```go\ndocumentedextension.Cache(\n\tdocumentedextension.KeyValue{",
		"documenteddependent.KeyValueConfiguration{",
		"In a Profile, this declares:\n\n```yaml\nkind: cache",
	} {
		if !strings.Contains(string(readme), want) {
			t.Errorf("README is missing %q\n%s", want, readme)
		}
	}
	bindings, err := os.ReadFile(filepath.Join(root.path, "bindings.go"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(bindings), "//\tdocumentedextension.Cache(") {
		t.Errorf("Godoc of the condition field does not show the example as Go:\n%s", bindings)
	}
}

// packageSymbols lists a package's exported names: types, constants and
// declaration functions.
func packageSymbols(ir *packageIR) []string {
	var names []string
	for _, key := range sortedTypeKeys(ir.types) {
		names = append(names, "type "+ir.types[key].request.name)
	}
	for _, constant := range ir.constants {
		names = append(names, "const "+constant.name)
	}
	for _, declaration := range ir.declarations {
		names = append(names, "func "+declaration.function.name)
	}
	sort.Strings(names)
	return names
}
