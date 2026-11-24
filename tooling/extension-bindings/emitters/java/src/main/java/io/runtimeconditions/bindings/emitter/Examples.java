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
 * {@code Bindings} call that produces it, in Javadoc and in the package README. An example for a
 * dependency's kind, such as an env-configuration field on a common-integrations cache, calls the
 * dependency's {@code Bindings} (see {@link Dependencies}). An example that cannot be rendered is
 * shown as Profile YAML instead.
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
        /** Fully qualified names to import, shared with a dependency's renderer. */
        final Set<String> imports;

        Renderer(EmissionPlan plan, JsonNode model, Set<String> imports) {
            this.plan = plan;
            this.model = model;
            this.imports = imports;
            for (TypePlan type : plan.types()) {
                types.put(type.name(), type);
            }
        }

        private void use(String name) {
            imports.add(plan.target().javaPackage() + "." + name);
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
            use(name);
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
            use(type.name());
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

        /**
         * Renders a Condition as a declaration call. Values come from this package first, then from
         * the dependency package that declares the kind, when one is given.
         */
        Node condition(JsonNode condition, Renderer dependency) throws ExampleException {
            String kind = condition.path("kind").asText();
            Renderer declarer = this;
            DeclarationPlan declaration = declaration(plan, kind);
            if (declaration == null && dependency != null) {
                declarer = dependency;
                declaration = declaration(dependency.plan, kind);
            }
            if (declaration == null) {
                throw new ExampleException("kind " + kind + " is not declared here");
            }
            List<Renderer> sources = declarer == this ? List.of(this) : List.of(this, declarer);
            List<Node> arguments = new ArrayList<>();
            JsonNode iface = condition.get("interface");
            String interfaceType = iface != null ? iface.path("type").asText("") : "";
            if (iface != null) {
                arguments.add(field(sources, kind, interfaceType, interfaceType, iface, "interface"));
            }
            List<String> keys = new ArrayList<>();
            condition.fieldNames().forEachRemaining(keys::add);
            keys.sort(null);
            for (String key : keys) {
                if (key.equals("kind") || key.equals("interface") || key.equals("name")) {
                    continue;
                }
                arguments.add(field(sources, kind, interfaceType, key, condition.get(key), "condition field"));
            }
            declarer.use(Plan.FUNCTIONS_CLASS);
            return new Node(Plan.FUNCTIONS_CLASS + "." + declaration.function() + "(", arguments, ")");
        }

        private static DeclarationPlan declaration(EmissionPlan plan, String kind) {
            DeclarationPlan found = null;
            for (DeclarationPlan item : plan.declarations()) {
                if (item.kind().equals(kind)) {
                    found = item;
                }
            }
            return found;
        }

        private static Node field(
                List<Renderer> sources, String kind, String interfaceType, String key, JsonNode value, String label)
                throws ExampleException {
            for (Renderer source : sources) {
                TypePlan type = source.root(kind, interfaceType, key);
                if (type == null) {
                    continue;
                }
                JsonNode item = value;
                boolean single = type.fields().size() == 1 && type.fields().get(0).sourceName().equals("value");
                if (single && !item.isObject()) {
                    ObjectNode wrapper = Json.MAPPER.createObjectNode();
                    wrapper.set("value", item);
                    item = wrapper;
                }
                return source.object(type, item);
            }
            throw new ExampleException(label + " " + key + " is not defined here");
        }
    }

    /** The declaration-field type of an interface or condition field root, if this package defines one. */
    static TypePlan rootType(EmissionPlan plan, JsonNode model, String kind, String interfaceType, String sourceName) {
        return new Renderer(plan, model, new TreeSet<>()).root(kind, interfaceType, sourceName);
    }

    /** A root-owned, documented condition field and the type that sets it. */
    record FieldDoc(String name, String kind, String interfaceType, String path, String description) {}

    /** The root extension's documented condition fields, in type-name order. */
    static List<FieldDoc> conditionFields(EmissionPlan plan, JsonNode model) {
        String root = model.path("rootExtension").path("id").asText();
        Renderer renderer = new Renderer(plan, model, new TreeSet<>());
        List<FieldDoc> docs = new ArrayList<>();
        for (JsonNode field : Json.list(model.path("vocabulary").path("conditionFields"))) {
            String description = field.path("description").asText("");
            if (!field.path("owner").asText().equals(root) || description.isBlank()) {
                continue;
            }
            List<JsonNode> segments = Json.list(field.path("segments"));
            String sourceName = segments.isEmpty()
                    ? field.path("path").asText()
                    : segments.get(segments.size() - 1).path("name").asText();
            String interfaceType = field.path("interfaceType").asText("");
            TypePlan type = renderer.root(field.path("kind").asText(), interfaceType, sourceName);
            if (type != null) {
                docs.add(new FieldDoc(
                        type.name(), field.path("kind").asText(), interfaceType, field.path("path").asText(), description));
            }
        }
        docs.sort(java.util.Comparator.comparing(FieldDoc::name));
        return docs;
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
        for (JsonNode field : Json.list(model.path("vocabulary").path("conditionFields"))) {
            if (field.path("owner").asText().equals(root)) {
                texts.add(field.path("description").asText(""));
            }
        }
        Map<String, EmissionPlan> dependencies = new HashMap<>();
        Map<String, Rendered> rendered = new LinkedHashMap<>();
        for (String text : texts) {
            for (Segment segment : split(text)) {
                if (segment.condition() == null || rendered.containsKey(segment.source())) {
                    continue;
                }
                Set<String> imports = new TreeSet<>();
                Renderer renderer = new Renderer(plan, model, imports);
                Renderer provider = null;
                String kind = segment.condition().path("kind").asText();
                for (Plan.ImportedDeclarationPlan imported : plan.importedDeclarations()) {
                    if (imported.kind().equals(kind)) {
                        EmissionPlan dependency = dependencies.computeIfAbsent(
                                imported.owner(), owner -> Dependencies.plan(plan, model, owner));
                        if (dependency != null) {
                            provider = new Renderer(dependency, model, imports);
                        }
                    }
                }
                try {
                    String code = renderer.condition(segment.condition(), provider).layout(0) + ";";
                    Set<String> simple = new TreeSet<>();
                    for (String name : imports) {
                        if (!simple.add(name.substring(name.lastIndexOf('.') + 1))) {
                            throw new ExampleException("two imports share the simple name of " + name);
                        }
                    }
                    rendered.put(segment.source(), new Rendered(code, imports));
                } catch (ExampleException skipped) {
                    // Shown as Profile YAML instead.
                }
            }
        }
        return rendered;
    }
}
