package normalizer

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const exampleExtension = `apiVersion: runtimeconditions.io/v1alpha1
kind: RuntimeConditionsExtensionDefinition
metadata:
  id: https://runtimeconditions.io/conformance/examples:1.0.0
  version: 1.0.0
spec:
  kinds:
    - name: cache
      description: |
        Keeps data in a cache.

        ` + "```condition" + `
        kind: cache
        interface:
          type: key_value
          engine: ENGINE
        ` + "```" + `
  interfaceTypes:
    - {name: key_value, targetKind: cache}
  interfaceFields:
    - {name: engine, targetKind: cache, targetType: key_value}
  fieldValues:
    - {field: interface.engine, targetKind: cache, targetType: key_value, values: [redis, memcached]}
  schemas:
    - id: cache-key-value
      appliesToKind: cache
      appliesToInterfaceType: key_value
      description: Validates key/value caches.
      schema:
        type: object
        properties:
          interface:
            type: object
            properties:
              engine: {enum: [redis, memcached]}
`

func normalizeExample(t *testing.T, engine string) error {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "root.yaml"), []byte(strings.Replace(exampleExtension, "ENGINE", engine, 1)), 0o644); err != nil {
		t.Fatal(err)
	}
	schemas := testSchemas(t)
	resolver, err := NewResolver(ResolverConfig{Schemas: schemas, CatalogRoots: []string{root}})
	if err != nil {
		t.Fatal(err)
	}
	closure, err := resolver.Resolve(context.Background(), "https://runtimeconditions.io/conformance/examples:1.0.0")
	if err != nil {
		t.Fatal(err)
	}
	_, err = Normalize(closure, BuildDependencyLock(closure), schemas, testNormalizeConfig())
	return err
}

func TestExampleConditionsAreValidated(t *testing.T) {
	if err := normalizeExample(t, "redis"); err != nil {
		t.Fatalf("a valid example should normalize: %v", err)
	}
	err := normalizeExample(t, "redsi")
	if err == nil || !strings.Contains(err.Error(), "RCB1316") || !strings.Contains(err.Error(), "/spec/kinds/0/description") {
		t.Fatalf("expected RCB1316 for the invalid example, got %v", err)
	}
}

func TestSplitDocumentation(t *testing.T) {
	segments, err := SplitDocumentation("Intro.\n\n```condition\nkind: cache\n```\n\nAfter.\n\n```yaml\nnot: an example\n```")
	if err != nil {
		t.Fatal(err)
	}
	if len(segments) != 3 || segments[0].Text != "Intro." || segments[1].Condition["kind"] != "cache" || !strings.Contains(segments[2].Text, "```yaml") {
		t.Fatalf("unexpected segments: %+v", segments)
	}
	if _, err := SplitDocumentation("```condition\nkind: cache\n"); err == nil {
		t.Fatal("an unclosed example should be rejected")
	}
}
