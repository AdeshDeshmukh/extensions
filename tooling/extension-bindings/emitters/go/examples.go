package goemitter

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/format"
	"sort"
	"strconv"
	"strings"

	"github.com/runtimeconditions/extensions/tooling/extension-bindings/normalizer"
)

// Descriptions may contain example Conditions in ```condition fences (a
// first-party documentation convention; see normalizer/examples.go). The Go
// emitter renders each example as the Go declaration that produces it, in Godoc
// and in the package README. An example for a dependency's kind, such as an
// env-configuration field on a common-integrations cache, calls the dependency's
// declaration function (see dependencies.go). An example that cannot be
// rendered is shown as the Profile YAML instead.

// writeDocs writes a description as Go comments: prose is wrapped, and each
// example Condition becomes an indented Go code block. prefix, when set, starts
// the first paragraph (for example "KeyValue: ").
func (ir *packageIR) writeDocs(buffer *bytes.Buffer, indent, prefix, text string) {
	segments, err := normalizer.SplitDocumentation(text)
	if err != nil {
		// The normalizer has already rejected malformed examples; keep the text.
		segments = []normalizer.DocSegment{{Text: text}}
	}
	for i, segment := range segments {
		if i > 0 {
			fmt.Fprintf(buffer, "%s//\n", indent)
		}
		if segment.Condition == nil {
			body := segment.Text
			if i == 0 {
				body = prefix + body
			}
			writeDocComment(buffer, indent, body)
			continue
		}
		code, ok := ir.exampleCode(segment.Condition)
		if !ok {
			code = strings.TrimSpace(dedentText(segment.Source))
		}
		for _, line := range strings.Split(code, "\n") {
			if line == "" {
				fmt.Fprintf(buffer, "%s//\n", indent)
				continue
			}
			fmt.Fprintf(buffer, "%s//\t%s\n", indent, line)
		}
	}
	if len(segments) == 0 && prefix != "" {
		writeDocComment(buffer, indent, strings.TrimSuffix(strings.TrimSpace(prefix), ":"))
	}
}

// exampleCode renders an example Condition as a call of this package's
// declaration function, formatted by gofmt.
func (ir *packageIR) exampleCode(condition map[string]any) (string, bool) {
	expression, err := ir.exampleExpression(condition)
	if err != nil {
		return "", false
	}
	source := "package example\n\nfunc _() {\n" + expression + "\n}\n"
	formatted, err := format.Source([]byte(source))
	if err != nil {
		return "", false
	}
	lines := strings.Split(strings.TrimSpace(string(formatted)), "\n")
	// Drop the package clause and the function wrapper; keep the statement.
	var body []string
	for _, line := range lines[3 : len(lines)-1] {
		body = append(body, strings.TrimPrefix(line, "\t"))
	}
	return strings.Join(body, "\n"), true
}

func (ir *packageIR) exampleExpression(condition map[string]any) (string, error) {
	kind, _ := condition["kind"].(string)
	// Values come from this package first, then from the package that declares
	// the kind when it is a direct dependency.
	type source struct {
		ir    *packageIR
		alias string
	}
	sources := []source{{ir, ir.target.PackageName}}
	declaration := ir.ownedDeclaration(kind)
	declarationAlias := ir.target.PackageName
	if declaration == nil {
		if imported, ok := ir.declarationsByKind[kind]; ok {
			if dependency, alias, found := ir.dependencyIR(imported.Owner); found {
				declaration = dependency.ownedDeclaration(kind)
				declarationAlias = alias
				sources = append(sources, source{dependency, alias})
			}
		}
	}
	if declaration == nil {
		return "", fmt.Errorf("kind %q is not declared by this package or a direct dependency", kind)
	}
	render := func(role, interfaceType, key string, value any) (string, error) {
		for _, candidate := range sources {
			if reference, found := candidate.ir.rootValue(role, kind, interfaceType, key); found {
				return candidate.ir.valueExpression(reference, value, candidate.alias)
			}
		}
		return "", fmt.Errorf("%s %q is not defined for kind %q", role, key, kind)
	}
	interfaceType := ""
	var arguments []string
	if value, ok := condition["interface"].(map[string]any); ok {
		interfaceType, _ = value["type"].(string)
		expression, err := render("interface", interfaceType, "interface", value)
		if err != nil {
			return "", err
		}
		arguments = append(arguments, expression)
	}
	keys := make([]string, 0, len(condition))
	for key := range condition {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		switch key {
		case "kind", "interface", "name":
			// The Condition name is not part of the declaration API.
			continue
		}
		expression, err := render("condition-field", interfaceType, key, condition[key])
		if err != nil {
			return "", err
		}
		arguments = append(arguments, expression)
	}
	call := declarationAlias + "." + declaration.function.name + "("
	if len(arguments) > 0 {
		call += "\n" + strings.Join(arguments, ",\n") + ",\n"
	}
	return call + ")", nil
}

// ownedDeclaration returns this package's declaration of kind, or nil.
func (ir *packageIR) ownedDeclaration(kind string) *declarationIR {
	for _, item := range ir.declarations {
		if item.model.Kind == kind {
			return item
		}
	}
	return nil
}

// rootValue finds the generated type of an interface or condition field root.
func (ir *packageIR) rootValue(role, kind, interfaceType, sourceName string) (typeReference, bool) {
	for _, binding := range ir.rootBindings {
		if binding.role != role || binding.scope.Kind != kind || binding.sourceName != sourceName && role == "condition-field" {
			continue
		}
		if role == "interface" && binding.fixedInterfaceType != interfaceType {
			continue
		}
		if role == "condition-field" && binding.scope.InterfaceType != "" && binding.scope.InterfaceType != interfaceType {
			continue
		}
		return binding.value, true
	}
	return typeReference{}, false
}

// valueExpression renders value as a Go expression of the referenced type.
func (ir *packageIR) valueExpression(reference typeReference, value any, alias string) (string, error) {
	var expression string
	if reference.key == "" {
		literal, err := goLiteral(reference.builtin, value)
		if err != nil {
			return "", err
		}
		expression = literal
	} else {
		rendered, err := ir.typedExpression(reference.key, value, alias)
		if err != nil {
			return "", err
		}
		expression = rendered
	}
	if !reference.pointer {
		return expression, nil
	}
	if reference.key != "" && ir.types[reference.key].kind == "struct" {
		return "&" + expression, nil
	}
	nativeType := reference.builtin
	if reference.key != "" {
		nativeType = alias + "." + ir.types[reference.key].request.name
	}
	return "&[]" + nativeType + "{" + expression + "}[0]", nil
}

func (ir *packageIR) typedExpression(key string, value any, alias string) (string, error) {
	typeValue := ir.types[key]
	qualified := alias + "." + typeValue.request.name
	switch typeValue.kind {
	case "scalar":
		if text, ok := value.(string); ok {
			for _, constant := range ir.constants {
				if constant.typeKey == key && constant.value == text {
					return alias + "." + constant.name, nil
				}
			}
		}
		literal, err := goLiteral(typeValue.underlying, value)
		if err != nil {
			return "", err
		}
		return qualified + "(" + literal + ")", nil
	case "struct":
		object, ok := value.(map[string]any)
		if !ok {
			return "", fmt.Errorf("%s expects an object", typeValue.request.name)
		}
		known := map[string]bool{"type": true}
		var fields []string
		for _, field := range typeValue.fields {
			known[field.sourceName] = true
			fieldValue, present := object[field.sourceName]
			if !present {
				continue
			}
			expression, err := ir.valueExpression(field.typeRef, fieldValue, alias)
			if err != nil {
				return "", err
			}
			fields = append(fields, field.name+": "+expression)
		}
		for name := range object {
			if !known[name] {
				return "", fmt.Errorf("%s has no field for %q", typeValue.request.name, name)
			}
		}
		if len(fields) == 0 {
			return qualified + "{}", nil
		}
		return qualified + "{\n" + strings.Join(fields, ",\n") + ",\n}", nil
	case "slice":
		items, ok := value.([]any)
		if !ok {
			return "", fmt.Errorf("%s expects a list", typeValue.request.name)
		}
		var elements []string
		for _, item := range items {
			expression, err := ir.valueExpression(typeValue.element, item, alias)
			if err != nil {
				return "", err
			}
			elements = append(elements, expression)
		}
		if len(elements) == 0 {
			return qualified + "{}", nil
		}
		return qualified + "{\n" + strings.Join(elements, ",\n") + ",\n}", nil
	case "map":
		object, ok := value.(map[string]any)
		if !ok {
			return "", fmt.Errorf("%s expects a mapping", typeValue.request.name)
		}
		names := make([]string, 0, len(object))
		for name := range object {
			names = append(names, name)
		}
		sort.Strings(names)
		var entries []string
		for _, name := range names {
			expression, err := ir.valueExpression(typeValue.element, object[name], alias)
			if err != nil {
				return "", err
			}
			entries = append(entries, strconv.Quote(name)+": "+expression)
		}
		if len(entries) == 0 {
			return qualified + "{}", nil
		}
		return qualified + "{\n" + strings.Join(entries, ",\n") + ",\n}", nil
	case "union":
		for _, variant := range typeValue.unionVariants {
			if expression, err := ir.typedExpression(variant, value, alias); err == nil {
				return expression, nil
			}
		}
		return "", fmt.Errorf("no variant of %s matches the example", typeValue.request.name)
	}
	return "", fmt.Errorf("%s cannot be shown as an example", typeValue.request.name)
}

func goLiteral(builtin string, value any) (string, error) {
	switch typed := value.(type) {
	case string:
		if builtin == "string" {
			return strconv.Quote(typed), nil
		}
	case bool:
		if builtin == "bool" {
			return strconv.FormatBool(typed), nil
		}
	case json.Number:
		switch builtin {
		case "int64", "int":
			if _, err := typed.Int64(); err == nil {
				return typed.String(), nil
			}
		case "float64":
			return typed.String(), nil
		}
	case float64:
		switch builtin {
		case "int64", "int":
			if typed == float64(int64(typed)) {
				return strconv.FormatInt(int64(typed), 10), nil
			}
		case "float64":
			return strconv.FormatFloat(typed, 'g', -1, 64), nil
		}
	}
	return "", fmt.Errorf("value %v does not fit Go type %s", value, builtin)
}

func dedentText(text string) string {
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
	for i, line := range lines {
		if common > 0 && len(line) >= common {
			lines[i] = line[common:]
		}
	}
	return strings.Join(lines, "\n")
}

// renderReadme writes the package README from the extension's documentation.
// It returns nil when the root extension has no description, so packages of
// undocumented extensions keep exactly the files they had before.
func (ir *packageIR) renderReadme() []byte {
	summary := ir.rootDescription()
	if summary == "" {
		return nil
	}
	var buffer bytes.Buffer
	fmt.Fprintf(&buffer, "<!-- Code generated by %s %s. DO NOT EDIT. -->\n\n", EmitterName, EmitterVersion)
	fmt.Fprintf(&buffer, "# %s\n\n", ir.target.ModulePath)
	ir.writeMarkdown(&buffer, summary)
	fmt.Fprintf(&buffer, "\n```sh\ngo get %s@%s\n```\n\n", ir.target.ModulePath, ir.target.Version)
	fmt.Fprintf(&buffer, "Runtime Conditions extension: %s\n", ir.model.RootExtension.ID)
	declarations := append([]*declarationIR(nil), ir.declarations...)
	sort.Slice(declarations, func(i, j int) bool { return declarations[i].function.name < declarations[j].function.name })
	for _, declaration := range declarations {
		fmt.Fprintf(&buffer, "\n## %s\n\n", declaration.function.name)
		fmt.Fprintf(&buffer, "`%s.%s` declares a `%s` Condition.\n", ir.target.PackageName, declaration.function.name, declaration.model.Kind)
		if declaration.model.Description != "" {
			buffer.WriteString("\n")
			ir.writeMarkdown(&buffer, declaration.model.Description)
		}
	}
	for _, group := range ir.conditionFieldGroups() {
		fmt.Fprintf(&buffer, "\n## %s\n\n", strings.Join(group.names, ", "))
		for index, name := range group.names {
			field := group.fields[index]
			target := "`" + field.Kind + "` Conditions"
			if field.InterfaceType != "" {
				target += " with interface type `" + field.InterfaceType + "`"
			}
			fmt.Fprintf(&buffer, "`%s.%s` sets the `%s` field of %s.\n", ir.target.PackageName, name, field.Path, target)
		}
		buffer.WriteString("\n")
		ir.writeMarkdown(&buffer, group.description)
	}
	buffer.WriteString("\nThe full API, with every type, field and constant documented, is in the Go package documentation.\n")
	return buffer.Bytes()
}

type conditionFieldGroup struct {
	description string
	names       []string
	fields      []normalizer.FieldModel
}

// conditionFieldGroups returns this package's documented condition fields,
// grouping fields that share one description, such as a field that applies to
// two interface types, in the order of their generated type names.
func (ir *packageIR) conditionFieldGroups() []conditionFieldGroup {
	type entry struct {
		name  string
		field normalizer.FieldModel
	}
	var entries []entry
	for _, field := range ir.model.Vocabulary.ConditionFields {
		typeValue, ok := ir.types["condition-field:"+field.Coordinate]
		if field.Owner != ir.model.RootExtension.ID || field.Description == "" || !ok {
			continue
		}
		entries = append(entries, entry{typeValue.request.name, field})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].name < entries[j].name })
	var groups []conditionFieldGroup
	index := map[string]int{}
	for _, item := range entries {
		position, ok := index[item.field.Description]
		if !ok {
			position = len(groups)
			index[item.field.Description] = position
			groups = append(groups, conditionFieldGroup{description: item.field.Description})
		}
		groups[position].names = append(groups[position].names, item.name)
		groups[position].fields = append(groups[position].fields, item.field)
	}
	return groups
}

// writeMarkdown writes prose as it is and each example as Go code followed by
// the Condition it produces in a Profile.
func (ir *packageIR) writeMarkdown(buffer *bytes.Buffer, text string) {
	segments, err := normalizer.SplitDocumentation(text)
	if err != nil {
		segments = []normalizer.DocSegment{{Text: text}}
	}
	for i, segment := range segments {
		if i > 0 {
			buffer.WriteString("\n")
		}
		if segment.Condition == nil {
			buffer.WriteString(segment.Text + "\n")
			continue
		}
		profile := strings.TrimSpace(dedentText(segment.Source))
		if code, ok := ir.exampleCode(segment.Condition); ok {
			fmt.Fprintf(buffer, "```go\n%s\n```\n\nIn a Profile, this declares:\n\n", code)
		}
		fmt.Fprintf(buffer, "```yaml\n%s\n```\n", profile)
	}
}
