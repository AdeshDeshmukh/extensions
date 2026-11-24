package io.runtimeconditions.bindings.emitter;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.runtimeconditions.bindings.emitter.Plan.DeclarationPlan;
import io.runtimeconditions.bindings.emitter.Plan.EmissionPlan;
import io.runtimeconditions.bindings.emitter.Plan.FieldPlan;
import io.runtimeconditions.bindings.emitter.Plan.Kind;
import io.runtimeconditions.bindings.emitter.Plan.TypeExpr;
import io.runtimeconditions.bindings.emitter.Plan.TypePlan;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.snakeyaml.engine.v2.api.Load;
import org.snakeyaml.engine.v2.api.LoadSettings;
import org.snakeyaml.engine.v2.exceptions.YamlEngineException;

/**
 * Renders example Conditions from extension descriptions as Java code.
 *
 * <p>Descriptions may contain example Conditions in {@code condition} fences, a first-party
 * documentation convention checked by the normalizer. The Java emitter renders each example as the
 * {@code Bindings} call that produces it, in Javadoc and in the package README. An example this
 * package cannot express on its own is shown as Profile YAML instead.
 */
final class Examples {
    static final String CONDITION_FENCE = "condition";
    private static final int WIDTH = 100;

    record Segment(String text, String source, JsonNode condition) {}

    record Rendered(String code, Set<String> imports) {}

    private static final class ExampleException extends Exception {
        ExampleException(String message) {
            super(message);
        }
    }

    private Examples() {}

    static String dedent(String text) {
        String[] lines = text.split("\n", -1);
        int common = Integer.MAX_VALUE;
        for (String line : lines) {
            if (!line.isBlank()) {
                common = Math.min(common, line.length() - line.stripLeading().length());
            }
        }
        if (common == Integer.MAX_VALUE) {
            return text;
        }
        StringBuilder builder = new StringBuilder();
        for (int index = 0; index < lines.length; index += 1) {
            String line = lines[index];
            int indent = line.length() - line.stripLeading().length();
            builder.append(line.substring(Math.min(common, indent)));
            if (index + 1 < lines.length) {
                builder.append('\n');
            }
        }
        return builder.toString();
    }

    /** The first paragraph of a description, on one line, for registry summaries. */
    static String summary(String text) {
        for (Segment segment : split(text)) {
            if (segment.condition() == null && segment.text() != null && !segment.text().isBlank()) {
                return segment.text().split("\\n\\s*\\n", 2)[0].strip().replaceAll("\\s+", " ");
            }
        }
        return "";
    }

    /** Splits a description into prose and example Conditions. */
    static List<Segment> split(String text) {
        List<Segment> segments = new ArrayList<>();
        List<String> prose = new ArrayList<>();
        List<String> example = null;
        for (String line : (text == null ? "" : text).split("\n", -1)) {
            String trimmed = line.strip();
            if (example == null && trimmed.equals("```" + CONDITION_FENCE)) {
                flush(segments, prose);
                example = new ArrayList<>();
            } else if (example != null && trimmed.equals("```")) {
                String source = String.join("\n", example);
                JsonNode condition = parse(source);
                if (condition != null && condition.isObject()) {
                    segments.add(new Segment(null, source, condition));
                } else {
                    prose.add("```yaml");
                    prose.add(source);
                    prose.add("```");
                }
                example = null;
            } else if (example != null) {
                example.add(line);
            } else {
                prose.add(line);
            }
        }
        if (example != null) {
            prose.add("```" + CONDITION_FENCE);
            prose.addAll(example);
        }
        flush(segments, prose);
        return segments;
    }

    private static void flush(List<Segment> segments, List<String> prose) {
        String joined = String.join("\n", prose).strip();
        if (!joined.isEmpty()) {
            segments.add(new Segment(joined, null, null));
        }
        prose.clear();
    }

    private static JsonNode parse(String source) {
        try {
            Object value = new Load(LoadSettings.builder().setAllowDuplicateKeys(false).build())
                    .loadFromString(dedent(source));
            return value == null ? null : Json.MAPPER.valueToTree(value);
        } catch (YamlEngineException | IllegalArgumentException error) {
            return null;
        }
    }

    // A small syntax tree laid out like google-java-format lays out calls.
    private record Node(String head, List<Node> arguments, String tail) {
        static Node atom(String text) {
            return new Node(text, null, null);
        }

        String flat() {
            if (arguments == null) {
                return head;
            }
            List<String> parts = new ArrayList<>();
            for (Node argument : arguments) {
                parts.add(argument.flat());
            }
            return head + String.join(", ", parts) + tail;
        }

        String layout(int indent) {
            String flat = flat();
            if (arguments == null || arguments.isEmpty() || indent + flat.length() <= WIDTH) {
                return flat;
            }
            String pad = " ".repeat(indent + 4);
            List<String> parts = new ArrayList<>();
            for (Node argument : arguments) {
                parts.add(pad + argument.layout(indent + 4));
            }
            return head + "\n" + String.join(",\n", parts) + tail;
        }
    }

    private static final class Renderer {
        private final EmissionPlan plan;
        private final JsonNode model;
        private final Map<String, TypePlan> types = new HashMap<>();
        final Set<String> imports = new TreeSet<>();

        Renderer(EmissionPlan plan, JsonNode model) {
            this.plan = plan;
            this.model = model;
            for (TypePlan type : plan.types()) {
                types.put(type.name(), type);
            }
        }

        Node value(TypeExpr expression, JsonNode value) throws ExampleException {
            switch (expression.kind()) {
                case "string":
                    if (value.isTextual()) {
                        return Node.atom(Source.literal(value.asText()));
                    }
                    break;
                case "boolean":
                    if (value.isBoolean()) {
                        return Node.atom(String.valueOf(value.asBoolean()));
                    }
                    break;
                case "integer":
                    if (value.isIntegralNumber()) {
                        return Node.atom(value.asLong() + "L");
                    }
                    break;
                case "number":
                    if (value.isNumber()) {
                        return Node.atom(value.asDouble() + "");
                    }
                    break;
                case "null":
                    if (value.isNull()) {
                        return Node.atom("null");
                    }
                    break;
                case "array":
                    if (value.isArray()) {
                        List<Node> items = new ArrayList<>();
                        for (JsonNode item : value) {
                            items.add(value(expression.arguments().get(0), item));
                        }
                        imports.add("java.util.List");
                        return new Node("List.of(", items, ")");
                    }
                    break;
                case "map":
                    if (value.isObject()) {
                        List<Node> entries = new ArrayList<>();
                        List<String> names = new ArrayList<>();
                        value.fieldNames().forEachRemaining(names::add);
                        names.sort(null);
                        for (String name : names) {
                            entries.add(Node.atom(Source.literal(name)));
                            entries.add(value(expression.arguments().get(0), value.get(name)));
                        }
                        imports.add("java.util.Map");
                        return new Node("Map.of(", entries, ")");
                    }
                    break;
                default:
                    return named(plan.names().get(expression.symbolKey()), value);
            }
            throw new ExampleException("value does not fit " + expression.kind());
        }

        Node named(String name, JsonNode value) throws ExampleException {
            TypePlan type = types.get(name);
            if (type == null) {
                throw new ExampleException("unknown type " + name);
            }
            imports.add(name);
            if (type.kind() == Kind.ENUM) {
                for (Plan.Member member : type.members()) {
                    if (value.isTextual() && member.value().equals(value.asText())) {
                        return Node.atom(name + "." + member.name());
                    }
                }
                throw new ExampleException(value + " is not a " + name);
            }
            if (type.kind() == Kind.ALIAS) {
                TypeExpr expression = type.expression();
                if (expression.kind().equals("union")) {
                    for (int index = 0; index < expression.arguments().size(); index += 1) {
                        try {
                            Node inner = value(expression.arguments().get(index), value);
                            return new Node("new " + name + ".Variant" + (index + 1) + "(", List.of(inner), ")");
                        } catch (ExampleException ignored) {
                            // Try the next variant.
                        }
                    }
                    throw new ExampleException("no variant of " + name + " matches");
                }
                return new Node("new " + name + "(", List.of(value(expression, value)), ")");
            }
            return object(type, value);
        }

        Node object(TypePlan type, JsonNode value) throws ExampleException {
            if (!value.isObject()) {
                throw new ExampleException(type.name() + " expects an object");
            }
            Set<String> known = new TreeSet<>(List.of("type"));
            for (FieldPlan field : type.fields()) {
                known.add(field.sourceName());
            }
            for (Iterator<String> names = value.fieldNames(); names.hasNext(); ) {
                String name = names.next();
                if (!known.contains(name)) {
                    throw new ExampleException(type.name() + " has no field " + name);
                }
            }
            imports.add(type.name());
            // Records take every component in order; an absent optional one is null.
            List<Node> arguments = new ArrayList<>();
            for (FieldPlan field : type.fields()) {
                JsonNode fieldValue = value.get(field.sourceName());
                arguments.add(fieldValue == null ? Node.atom("null") : value(field.typeExpr(), fieldValue));
            }
            return new Node("new " + type.name() + "(", arguments, ")");
        }

        TypePlan root(String kind, String interfaceType, String sourceName) {
            Map<String, JsonNode> schemas = new LinkedHashMap<>();
            for (JsonNode schema : Json.list(model.path("schemas"))) {
                schemas.put(schema.path("coordinate").asText(), schema);
            }
            TypePlan best = null;
            boolean bestExact = false;
            for (TypePlan type : plan.types()) {
                JsonNode schema = schemas.get(type.schemaCoordinate());
                if (!type.isDeclarationField() || !type.sourceName().equals(sourceName) || schema == null) {
                    continue;
                }
                String schemaKind = schema.has("kind") ? schema.path("kind").asText() : schema.path("id").asText();
                boolean scoped = schema.has("interfaceType");
                if (!schemaKind.equals(kind) || (scoped && !schema.path("interfaceType").asText().equals(interfaceType))) {
                    continue;
                }
                if (best == null || (scoped && !bestExact)) {
                    best = type;
                    bestExact = scoped;
                }
            }
            return best;
        }

        Node condition(JsonNode condition) throws ExampleException {
            String kind = condition.path("kind").asText();
            DeclarationPlan declaration = null;
            for (DeclarationPlan item : plan.declarations()) {
                if (item.kind().equals(kind)) {
                    declaration = item;
                }
            }
            if (declaration == null) {
                throw new ExampleException("kind " + kind + " is not declared here");
            }
            List<Node> arguments = new ArrayList<>();
            JsonNode iface = condition.get("interface");
            String interfaceType = iface != null ? iface.path("type").asText("") : "";
            if (iface != null) {
                TypePlan type = root(kind, interfaceType, interfaceType);
                if (type == null) {
                    throw new ExampleException("interface " + interfaceType + " is not defined here");
                }
                arguments.add(object(type, iface));
            }
            List<String> keys = new ArrayList<>();
            condition.fieldNames().forEachRemaining(keys::add);
            keys.sort(null);
            for (String key : keys) {
                if (key.equals("kind") || key.equals("interface") || key.equals("name")) {
                    continue;
                }
                TypePlan type = root(kind, interfaceType, key);
                if (type == null) {
                    throw new ExampleException("condition field " + key + " is not defined here");
                }
                JsonNode item = condition.get(key);
                boolean single = type.fields().size() == 1 && type.fields().get(0).sourceName().equals("value");
                if (single && !item.isObject()) {
                    ObjectNode wrapper = Json.MAPPER.createObjectNode();
                    wrapper.set("value", item);
                    item = wrapper;
                }
                arguments.add(object(type, item));
            }
            imports.add(Plan.FUNCTIONS_CLASS);
            return new Node(Plan.FUNCTIONS_CLASS + "." + declaration.function() + "(", arguments, ")");
        }
    }

    /** Renders every example Condition in the model's descriptions, keyed by source. */
    static Map<String, Rendered> render(EmissionPlan plan, JsonNode model) {
        List<String> texts = new ArrayList<>();
        String root = model.path("rootExtension").path("id").asText();
        for (JsonNode extension : Json.list(model.path("extensions"))) {
            if (extension.path("id").asText().equals(root)) {
                texts.add(extension.path("description").asText(""));
            }
        }
        for (JsonNode owned : Json.list(model.path("vocabulary").path("ownedDeclarations"))) {
            texts.add(owned.path("description").asText(""));
        }
        Map<String, Rendered> rendered = new LinkedHashMap<>();
        for (String text : texts) {
            for (Segment segment : split(text)) {
                if (segment.condition() == null || rendered.containsKey(segment.source())) {
                    continue;
                }
                Renderer renderer = new Renderer(plan, model);
                try {
                    String code = renderer.condition(segment.condition()).layout(0) + ";";
                    rendered.put(segment.source(), new Rendered(code, renderer.imports));
                } catch (ExampleException skipped) {
                    // Shown as Profile YAML instead.
                }
            }
        }
        return rendered;
    }
}
