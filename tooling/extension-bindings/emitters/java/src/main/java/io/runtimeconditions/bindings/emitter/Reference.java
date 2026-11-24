package io.runtimeconditions.bindings.emitter;

import com.fasterxml.jackson.databind.JsonNode;
import io.runtimeconditions.bindings.emitter.Plan.EmissionPlan;
import io.runtimeconditions.bindings.emitter.Plan.FieldPlan;
import io.runtimeconditions.bindings.emitter.Plan.Kind;
import io.runtimeconditions.bindings.emitter.Plan.TypeExpr;
import io.runtimeconditions.bindings.emitter.Plan.TypePlan;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The README's "Conditions" reference: one collapsible entry per interface type and per condition
 * field the package defines, listing every field a user can set, its type, whether it is required,
 * what it means and its allowed values. It uses {@code <details>}, which GitHub renders as a line
 * that opens on click.
 */
final class Reference {
    private static final Pattern SENTENCE_END = Pattern.compile("\\.(\\s|$)");

    private Reference() {}

    private record Row(String path, String type, boolean required, String description) {}

    private record Child(TypePlan type, String suffix) {}

    /** Prose of a description on one line, without examples, safe inside a table cell. */
    private static String cell(String text) {
        List<String> prose = new ArrayList<>();
        for (Examples.Segment segment : Examples.split(text == null ? "" : text)) {
            if (segment.condition() == null && segment.text() != null) {
                prose.add(segment.text());
            }
        }
        return String.join(" ", prose).strip().replaceAll("\\s+", " ").replace("|", "\\|");
    }

    private static String headline(String text) {
        String first = Examples.summary(text == null ? "" : text);
        Matcher matcher = SENTENCE_END.matcher(first);
        return matcher.find() ? first.substring(0, matcher.start() + 1) : first;
    }

    private static String article(String word) {
        return !word.isEmpty() && "aeiouAEIOU".indexOf(word.charAt(0)) >= 0 ? "an" : "a";
    }

    private static List<Row> rows(EmissionPlan plan, TypePlan root) {
        Map<String, TypePlan> types = new HashMap<>();
        for (TypePlan type : plan.types()) {
            types.put(type.name(), type);
        }
        List<Row> rows = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        visit(plan, types, root, "", Set.of(root.name()), rows, seen);
        return rows;
    }

    private static TypePlan named(EmissionPlan plan, Map<String, TypePlan> types, TypeExpr expression) {
        if (expression.symbolKey() == null || expression.symbolKey().isEmpty()) {
            return null;
        }
        return types.get(plan.names().getOrDefault(expression.symbolKey(), ""));
    }

    /** The enum a field's values come from, directly or through an alias or a list. */
    private static TypePlan enumOf(EmissionPlan plan, Map<String, TypePlan> types, TypeExpr expression, Set<String> through) {
        if (expression.kind().equals("array")) {
            return enumOf(plan, types, expression.arguments().get(0), through);
        }
        TypePlan target = named(plan, types, expression);
        if (target == null || through.contains(target.name())) {
            return null;
        }
        if (target.kind() == Kind.ENUM) {
            return target;
        }
        if (target.kind() == Kind.ALIAS && target.expression() != null) {
            return enumOf(plan, types, target.expression(), with(through, target.name()));
        }
        return null;
    }

    /** The objects nested under a field, with the path suffix that reaches them. */
    private static List<Child> nested(
            EmissionPlan plan, Map<String, TypePlan> types, TypeExpr expression, String suffix, Set<String> through) {
        switch (expression.kind()) {
            case "array":
                return nested(plan, types, expression.arguments().get(0), suffix + "[]", through);
            case "map":
                return nested(plan, types, expression.arguments().get(0), suffix + ".*", through);
            case "union": {
                List<Child> children = new ArrayList<>();
                for (TypeExpr argument : expression.arguments()) {
                    children.addAll(nested(plan, types, argument, suffix, through));
                }
                return children;
            }
            default:
                break;
        }
        TypePlan target = named(plan, types, expression);
        if (target == null || through.contains(target.name())) {
            return List.of();
        }
        if (target.kind() == Kind.OBJECT) {
            return List.of(new Child(target, suffix));
        }
        if (target.kind() == Kind.ALIAS && target.expression() != null) {
            return nested(plan, types, target.expression(), suffix, with(through, target.name()));
        }
        return List.of();
    }

    private static void visit(
            EmissionPlan plan,
            Map<String, TypePlan> types,
            TypePlan current,
            String prefix,
            Set<String> stack,
            List<Row> rows,
            Set<String> seen) {
        for (FieldPlan field : current.fields()) {
            String path = prefix + field.name();
            if (!seen.add(path)) {
                continue;
            }
            List<String> parts = new ArrayList<>();
            parts.add(cell(field.description()));
            TypePlan values = enumOf(plan, types, field.typeExpr(), Set.of());
            if (values != null) {
                parts.add("Values:");
                for (Plan.Member member : values.members()) {
                    String meaning = cell(member.description());
                    parts.add(meaning.isEmpty() ? "`" + member.value() + "`" : "`" + member.value() + "`: " + meaning);
                }
            }
            parts.removeIf(String::isEmpty);
            rows.add(new Row(path, field.annotation(), field.required(), String.join("<br>", parts)));
            for (Child child : nested(plan, types, field.typeExpr(), "", Set.of())) {
                if (!stack.contains(child.type().name())) {
                    visit(plan, types, child.type(), path + child.suffix() + ".", with(stack, child.type().name()), rows, seen);
                }
            }
        }
    }

    private static Set<String> with(Set<String> values, String value) {
        Set<String> result = new HashSet<>(values);
        result.add(value);
        return result;
    }

    private static List<String> entry(EmissionPlan plan, String heading, String sentence, TypePlan type) {
        List<String> lines = new ArrayList<>(List.of("<details>", "<summary>" + heading + "</summary>", "", sentence, ""));
        List<Row> rows = rows(plan, type);
        if (rows.isEmpty()) {
            lines.add("It has no fields to set.");
            lines.add("");
        } else {
            lines.add("| Field | Type | Required | Description |");
            lines.add("| --- | --- | --- | --- |");
            for (Row row : rows) {
                lines.add("| `" + row.path() + "` | `" + row.type().replace("|", "\\|") + "` | "
                        + (row.required() ? "yes" : "no") + " | " + row.description() + " |");
            }
            lines.add("");
        }
        lines.add("</details>");
        return lines;
    }

    /** The README's collapsible reference of every interface and condition field this package defines. */
    static List<String> conditions(EmissionPlan plan, JsonNode model) {
        String root = model.path("rootExtension").path("id").asText();
        List<List<String>> entries = new ArrayList<>();
        List<JsonNode> interfaces = new ArrayList<>();
        for (JsonNode item : Json.list(model.path("vocabulary").path("interfaces"))) {
            if (item.path("owner").asText().equals(root)) {
                interfaces.add(item);
            }
        }
        interfaces.sort(Comparator.comparing((JsonNode item) -> item.path("kind").asText())
                .thenComparing(item -> item.path("type").asText()));
        for (JsonNode item : interfaces) {
            String kind = item.path("kind").asText();
            String interfaceType = item.path("type").asText();
            TypePlan type = Examples.rootType(plan, model, kind, interfaceType, interfaceType);
            if (type == null) {
                continue;
            }
            String about = headline(item.path("description").asText(""));
            String heading = "<b>" + kind + "</b> · <code>" + interfaceType + "</code>" + (about.isEmpty() ? "" : ": " + about);
            String sentence = "`" + type.name() + "` is the `interface` of " + article(kind) + " `" + kind + "` Condition.";
            entries.add(entry(plan, heading, sentence, type));
        }
        List<JsonNode> fields = new ArrayList<>();
        for (JsonNode item : Json.list(model.path("vocabulary").path("conditionFields"))) {
            if (item.path("owner").asText().equals(root)) {
                fields.add(item);
            }
        }
        fields.sort(Comparator.comparing((JsonNode item) -> item.path("kind").asText())
                .thenComparing(item -> item.path("interfaceType").asText("")));
        for (JsonNode item : fields) {
            String kind = item.path("kind").asText();
            String interfaceType = item.path("interfaceType").asText("");
            String path = item.path("path").asText();
            List<JsonNode> segments = Json.list(item.path("segments"));
            String sourceName = segments.isEmpty() ? path : segments.get(segments.size() - 1).path("name").asText();
            TypePlan type = Examples.rootType(plan, model, kind, interfaceType, sourceName);
            if (type == null) {
                continue;
            }
            String scope = interfaceType.isEmpty() ? "" : " · <code>" + interfaceType + "</code>";
            String about = headline(item.path("description").asText(""));
            String heading = "<code>" + path + "</code> on <b>" + kind + "</b>" + scope + (about.isEmpty() ? "" : ": " + about);
            String sentence = "`" + type.name() + "` sets the `" + path + "` field of " + article(kind) + " `" + kind + "` Condition.";
            entries.add(entry(plan, heading, sentence, type));
        }
        if (entries.isEmpty()) {
            return List.of();
        }
        List<String> lines = new ArrayList<>(List.of(
                "## Conditions",
                "",
                "Every Condition part this package defines, with the fields you can set. Select a line to open it."));
        for (List<String> item : entries) {
            lines.add("");
            lines.addAll(item);
        }
        return lines;
    }
}
