package normalizer

import (
	"bytes"
	"encoding/json"
	"fmt"
	"strings"

	"gopkg.in/yaml.v3"
)

// Example Conditions are a first-party documentation convention, not part of
// the extension specification: a description may contain fenced blocks
//
//	```condition
//	kind: cache
//	interface:
//	  type: key_value
//	  engine: redis
//	```
//
// that emitters render as native code in generated docs and READMEs. The
// normalizer checks every example against the schemas that apply to it, so
// published documentation cannot show a Condition the extension rejects.

// ConditionFence is the fence info string that marks an example Condition.
const ConditionFence = "condition"

// DocSegment is one part of a description: prose, or an example Condition.
type DocSegment struct {
	Text      string // prose, when Condition is nil
	Source    string // the example's YAML exactly as written
	Condition map[string]any
}

// SplitDocumentation splits a description into prose and example Conditions.
// Other fenced blocks stay in the prose unchanged.
func SplitDocumentation(text string) ([]DocSegment, error) {
	var segments []DocSegment
	var prose, example []string
	inExample := false
	flushProse := func() {
		if joined := strings.TrimSpace(strings.Join(prose, "\n")); joined != "" {
			segments = append(segments, DocSegment{Text: joined})
		}
		prose = nil
	}
	for _, line := range strings.Split(text, "\n") {
		trimmed := strings.TrimSpace(line)
		switch {
		case !inExample && trimmed == "```"+ConditionFence:
			flushProse()
			inExample = true
		case inExample && trimmed == "```":
			source := strings.Join(example, "\n")
			condition, err := decodeExample(source)
			if err != nil {
				return nil, err
			}
			segments = append(segments, DocSegment{Source: source, Condition: condition})
			example = nil
			inExample = false
		case inExample:
			example = append(example, line)
		default:
			prose = append(prose, line)
		}
	}
	if inExample {
		return nil, fmt.Errorf("example Condition is missing its closing fence")
	}
	flushProse()
	return segments, nil
}

func decodeExample(source string) (map[string]any, error) {
	var decoded any
	if err := yaml.Unmarshal([]byte(dedent(source)), &decoded); err != nil {
		return nil, fmt.Errorf("example Condition is not YAML: %w", err)
	}
	// A JSON round trip gives the plain JSON data model that schemas validate.
	encoded, err := json.Marshal(decoded)
	if err != nil {
		return nil, fmt.Errorf("example Condition is not JSON-compatible: %w", err)
	}
	var condition map[string]any
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	if err := decoder.Decode(&condition); err != nil || condition == nil {
		return nil, fmt.Errorf("example Condition must be a mapping")
	}
	if kind, _ := condition["kind"].(string); kind == "" {
		return nil, fmt.Errorf("example Condition needs a kind")
	}
	return condition, nil
}

// dedent removes the indentation common to every non-blank line.
func dedent(text string) string {
	lines := strings.Split(text, "\n")
	common := -1
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			continue
		}
		indent := len(line) - len(strings.TrimLeft(line, " \t"))
		if common < 0 || indent < common {
			common = indent
		}
	}
	if common <= 0 {
		return text
	}
	for i, line := range lines {
		if len(line) >= common {
			lines[i] = line[common:]
		} else {
			lines[i] = strings.TrimLeft(line, " \t")
		}
	}
	return strings.Join(lines, "\n")
}

// documentedTexts lists every description of a document with its JSON pointer.
func documentedTexts(definition ExtensionDefinition) [][2]string {
	texts := [][2]string{{"/metadata/description", definition.Metadata.Description}}
	for i, item := range definition.Spec.Kinds {
		texts = append(texts, [2]string{fmt.Sprintf("/spec/kinds/%d/description", i), item.Description})
	}
	for i, item := range definition.Spec.InterfaceTypes {
		texts = append(texts, [2]string{fmt.Sprintf("/spec/interfaceTypes/%d/description", i), item.Description})
	}
	for i, item := range definition.Spec.ConditionFields {
		texts = append(texts, [2]string{fmt.Sprintf("/spec/conditionFields/%d/description", i), item.Description})
	}
	for i, item := range definition.Spec.InterfaceFields {
		texts = append(texts, [2]string{fmt.Sprintf("/spec/interfaceFields/%d/description", i), item.Description})
	}
	return texts
}

// validateExamples checks every example Condition of the root extension against
// the closure: its kind and interface type must be defined, and every schema
// whose scope matches it must accept it.
func validateExamples(closure ResolvedClosure) error {
	root, ok := closure.ByID[closure.Root]
	if !ok {
		return nil
	}
	kinds := map[string]bool{}
	interfaces := map[string]bool{}
	for _, document := range closure.Documents {
		for _, kind := range document.Definition.Spec.Kinds {
			kinds[kind.Name] = true
		}
		for _, item := range document.Definition.Spec.InterfaceTypes {
			interfaces[item.TargetKind+"\x00"+item.Name] = true
		}
	}
	for _, entry := range documentedTexts(root.Definition) {
		pointer, text := entry[0], entry[1]
		if !strings.Contains(text, "```"+ConditionFence) {
			continue
		}
		segments, err := SplitDocumentation(text)
		if err != nil {
			return diagnostic("extension", "RCB1316", closure.Root, pointer, err.Error())
		}
		for _, segment := range segments {
			if segment.Condition == nil {
				continue
			}
			if err := checkExample(segment.Condition, closure, kinds, interfaces); err != nil {
				return diagnostic("extension", "RCB1316", closure.Root, pointer,
					fmt.Sprintf("example Condition is invalid: %v\n%s", err, strings.TrimSpace(dedent(segment.Source))))
			}
		}
	}
	return nil
}

func checkExample(condition map[string]any, closure ResolvedClosure, kinds, interfaces map[string]bool) error {
	kind, _ := condition["kind"].(string)
	if !kinds[kind] {
		return fmt.Errorf("kind %q is not defined by the extension or its dependencies", kind)
	}
	interfaceType := ""
	if iface, ok := condition["interface"].(map[string]any); ok {
		interfaceType, _ = iface["type"].(string)
		if interfaceType == "" || !interfaces[kind+"\x00"+interfaceType] {
			return fmt.Errorf("interface type %q is not defined for kind %q", interfaceType, kind)
		}
	}
	for _, document := range closure.Documents {
		for _, schema := range document.Definition.Spec.Schemas {
			if schema.AppliesToKind != "" && schema.AppliesToKind != kind {
				continue
			}
			if schema.AppliesToInterfaceType != "" && schema.AppliesToInterfaceType != interfaceType {
				continue
			}
			if err := validateExtensionSchemaFragment(schema.Schema, "", condition); err != nil {
				return fmt.Errorf("schema %s: %v", schema.ID, err)
			}
		}
	}
	return nil
}
