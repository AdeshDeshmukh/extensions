package goemitter

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/runtimeconditions/extensions/tooling/extension-bindings/normalizer"
)

// The README's "Conditions" reference: one collapsible entry per interface type
// and per condition field the package defines, listing every field a user can
// set, its type, whether it is required, what it means and its allowed values.
// It uses <details>, which GitHub renders as a line that opens on click.

var firstSentenceEnd = regexp.MustCompile(`\.(\s|$)`)

// referenceCell renders a description's prose on one line, without examples,
// safe inside a Markdown table cell.
func referenceCell(text string) string {
	segments, err := normalizer.SplitDocumentation(text)
	if err != nil {
		segments = []normalizer.DocSegment{{Text: text}}
	}
	var prose []string
	for _, segment := range segments {
		if segment.Condition == nil {
			prose = append(prose, segment.Text)
		}
	}
	return strings.ReplaceAll(strings.Join(strings.Fields(strings.Join(prose, " ")), " "), "|", `\|`)
}

// referenceHeadline is the first sentence of a description's first paragraph.
func referenceHeadline(text string) string {
	first := strings.Join(strings.Fields(strings.SplitN(strings.TrimSpace(firstProse(text)), "\n\n", 2)[0]), " ")
	if location := firstSentenceEnd.FindStringIndex(first); location != nil {
		return first[:location[0]+1]
	}
	return first
}

func firstProse(text string) string {
	segments, err := normalizer.SplitDocumentation(text)
	if err != nil {
		return text
	}
	for _, segment := range segments {
		if segment.Condition == nil && strings.TrimSpace(segment.Text) != "" {
			return segment.Text
		}
	}
	return ""
}

func referenceArticle(word string) string {
	if word != "" && strings.ContainsRune("aeiouAEIOU", rune(word[0])) {
		return "an"
	}
	return "a"
}

type referenceRow struct {
	path, typeName, description string
	required                    bool
}

// referenceRows lists the fields of a type and of every object nested under it.
func (ir *packageIR) referenceRows(key string) []referenceRow {
	var rows []referenceRow
	seen := map[string]bool{}
	// enumKey returns the scalar type with constants that a field's values come from.
	var enumKey func(reference typeReference, through map[string]bool) string
	enumKey = func(reference typeReference, through map[string]bool) string {
		typeValue, ok := ir.types[reference.key]
		if !ok || through[reference.key] {
			return ""
		}
		switch typeValue.kind {
		case "scalar":
			for _, constant := range ir.constants {
				if constant.typeKey == reference.key {
					return reference.key
				}
			}
		case "slice":
			return enumKey(typeValue.element, extend(through, reference.key))
		}
		return ""
	}
	type child struct{ key, suffix string }
	var nested func(key, suffix string, through map[string]bool) []child
	nested = func(key, suffix string, through map[string]bool) []child {
		typeValue, ok := ir.types[key]
		if !ok || through[key] {
			return nil
		}
		next := extend(through, key)
		switch typeValue.kind {
		case "struct":
			return []child{{key, suffix}}
		case "slice":
			return nested(typeValue.element.key, suffix+"[]", next)
		case "map":
			return nested(typeValue.element.key, suffix+".*", next)
		case "union":
			var children []child
			for _, variant := range typeValue.unionVariants {
				children = append(children, nested(variant, suffix, next)...)
			}
			return children
		}
		return nil
	}
	var visit func(key, prefix string, stack map[string]bool)
	visit = func(key, prefix string, stack map[string]bool) {
		for _, field := range ir.types[key].fields {
			path := prefix + field.name
			if seen[path] {
				continue
			}
			seen[path] = true
			parts := []string{referenceCell(field.description)}
			if values := enumKey(field.typeRef, map[string]bool{}); values != "" {
				parts = append(parts, "Values:")
				for _, constant := range ir.constants {
					if constant.typeKey != values {
						continue
					}
					if meaning := referenceCell(constant.description); meaning != "" {
						parts = append(parts, fmt.Sprintf("`%s`: %s", constant.value, meaning))
					} else {
						parts = append(parts, fmt.Sprintf("`%s`", constant.value))
					}
				}
			}
			var kept []string
			for _, part := range parts {
				if part != "" {
					kept = append(kept, part)
				}
			}
			rows = append(rows, referenceRow{
				path: path, typeName: ir.renderTypeReference(field.typeRef),
				required: field.required, description: strings.Join(kept, "<br>"),
			})
			for _, item := range nested(field.typeRef.key, "", map[string]bool{}) {
				if !stack[item.key] {
					visit(item.key, path+item.suffix+".", extend(stack, item.key))
				}
			}
		}
	}
	visit(key, "", map[string]bool{key: true})
	return rows
}

func extend(values map[string]bool, key string) map[string]bool {
	copied := cloneBoolMap(values)
	copied[key] = true
	return copied
}

func (ir *packageIR) referenceEntry(buffer *strings.Builder, heading, sentence, key string) {
	fmt.Fprintf(buffer, "\n<details>\n<summary>%s</summary>\n\n%s\n\n", heading, sentence)
	rows := ir.referenceRows(key)
	if len(rows) == 0 {
		buffer.WriteString("It has no fields to set.\n\n")
	} else {
		buffer.WriteString("| Field | Type | Required | Description |\n| --- | --- | --- | --- |\n")
		for _, row := range rows {
			required := "no"
			if row.required {
				required = "yes"
			}
			fmt.Fprintf(buffer, "| `%s` | `%s` | %s | %s |\n", row.path, strings.ReplaceAll(row.typeName, "|", `\|`), required, row.description)
		}
		buffer.WriteString("\n")
	}
	buffer.WriteString("</details>\n")
}

// conditionsReference renders the README's collapsible reference of every
// interface and condition field this package defines, or "" when there is none.
func (ir *packageIR) conditionsReference() string {
	var buffer strings.Builder
	alias := ir.target.PackageName
	interfaces := append([]normalizer.InterfaceModel(nil), ir.model.Vocabulary.Interfaces...)
	sort.Slice(interfaces, func(i, j int) bool {
		if interfaces[i].Kind != interfaces[j].Kind {
			return interfaces[i].Kind < interfaces[j].Kind
		}
		return interfaces[i].Type < interfaces[j].Type
	})
	for _, item := range interfaces {
		reference, ok := ir.rootValue("interface", item.Kind, item.Type, "interface")
		if item.Owner != ir.model.RootExtension.ID || !ok || reference.key == "" {
			continue
		}
		heading := fmt.Sprintf("<b>%s</b> · <code>%s</code>", item.Kind, item.Type)
		if about := referenceHeadline(item.Description); about != "" {
			heading += ": " + about
		}
		sentence := fmt.Sprintf("`%s.%s` is the `interface` of %s `%s` Condition.",
			alias, ir.types[reference.key].request.name, referenceArticle(item.Kind), item.Kind)
		ir.referenceEntry(&buffer, heading, sentence, reference.key)
	}
	fields := append([]normalizer.FieldModel(nil), ir.model.Vocabulary.ConditionFields...)
	sort.Slice(fields, func(i, j int) bool {
		if fields[i].Kind != fields[j].Kind {
			return fields[i].Kind < fields[j].Kind
		}
		return fields[i].InterfaceType < fields[j].InterfaceType
	})
	for _, field := range fields {
		typeValue, ok := ir.types["condition-field:"+field.Coordinate]
		if field.Owner != ir.model.RootExtension.ID || !ok {
			continue
		}
		heading := fmt.Sprintf("<code>%s</code> on <b>%s</b>", field.Path, field.Kind)
		if field.InterfaceType != "" {
			heading += fmt.Sprintf(" · <code>%s</code>", field.InterfaceType)
		}
		if about := referenceHeadline(field.Description); about != "" {
			heading += ": " + about
		}
		sentence := fmt.Sprintf("`%s.%s` sets the `%s` field of %s `%s` Condition.",
			alias, typeValue.request.name, field.Path, referenceArticle(field.Kind), field.Kind)
		ir.referenceEntry(&buffer, heading, sentence, typeValue.key)
	}
	if buffer.Len() == 0 {
		return ""
	}
	return "## Conditions\n\nEvery Condition part this package defines, with the fields you can set. Select a line to open it.\n" + buffer.String()
}
