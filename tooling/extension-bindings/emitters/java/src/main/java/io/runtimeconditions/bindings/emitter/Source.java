package io.runtimeconditions.bindings.emitter;

import static io.runtimeconditions.bindings.emitter.DiagnosticError.fail;

import com.fasterxml.jackson.databind.JsonNode;
import io.runtimeconditions.bindings.emitter.Plan.DeclarationPlan;
import io.runtimeconditions.bindings.emitter.Plan.EmissionPlan;
import io.runtimeconditions.bindings.emitter.Plan.FieldPlan;
import io.runtimeconditions.bindings.emitter.Plan.Kind;
import io.runtimeconditions.bindings.emitter.Plan.TypePlan;
import io.runtimeconditions.bindings.emitter.Target.PackageTarget;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.stream.Stream;

public final class Source {
    private Source() {}

    public static String sourceRoot(EmissionPlan plan) {
        return "src/main/java/" + plan.target().javaPackage().replace('.', '/') + "/";
    }

    public static String sourcePath(EmissionPlan plan, String name) {
        return sourceRoot(plan) + name + ".java";
    }

    public static List<String> exportedNames(EmissionPlan plan) {
        TreeSet<String> names = new TreeSet<>();
        if (!plan.declarations().isEmpty()) {
            names.add(Plan.DECLARATION_CLASS);
            names.add(Plan.FUNCTIONS_CLASS);
        }
        for (DeclarationPlan item : plan.declarations()) {
            names.add(item.protocol());
        }
        for (TypePlan item : plan.types()) {
            names.add(item.name());
        }
        return new ArrayList<>(names);
    }

    static String literal(String value) {
        StringBuilder builder = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index += 1) {
            char character = value.charAt(index);
            switch (character) {
                case '"' -> builder.append("\\\"");
                case '\\' -> builder.append("\\\\");
                case '\n' -> builder.append("\\n");
                case '\r' -> builder.append("\\r");
                case '\t' -> builder.append("\\t");
                default -> {
                    if (character < 0x20 || character > 0x7e) {
                        builder.append(String.format("\\u%04x", (int) character));
                    } else {
                        builder.append(character);
                    }
                }
            }
        }
        return builder.append('"').toString();
    }

    /**
     * Renders extension documentation as Javadoc. Descriptions come from the
     * extension's vocabulary and JSON Schema; empty text renders nothing. Text is
     * HTML-escaped so values such as {@code >=1.2.0} stay valid Javadoc.
     */
    // Examples rendered for the package being emitted; renderSources sets them.
    private static final ThreadLocal<Map<String, Examples.Rendered>> CURRENT_EXAMPLES =
            ThreadLocal.withInitial(Map::of);

    static List<String> javadoc(String text, List<String[]> params, String indent) {
        List<String> body = new ArrayList<>();
        for (Examples.Segment segment : Examples.split(text == null ? "" : text.strip())) {
            if (segment.condition() == null) {
                for (String paragraph : paragraphs(segment.text())) {
                    if (!body.isEmpty()) {
                        body.add("");
                    }
                    body.addAll(wrap((body.isEmpty() ? "" : "<p>") + paragraph, indent, ""));
                }
                continue;
            }
            // An example Condition becomes Java code, or Profile YAML if it cannot.
            Examples.Rendered rendered = CURRENT_EXAMPLES.get().get(segment.source());
            String code = rendered != null ? rendered.code() : Examples.dedent(segment.source()).strip();
            if (!body.isEmpty()) {
                body.add("");
            }
            body.add("<pre>{@code");
            for (String line : code.split("\n")) {
                body.add(line.replace("*/", "*\\/"));
            }
            body.add("}</pre>");
        }
        boolean first = true;
        for (String[] param : params) {
            List<String> parts = paragraphs(param[1]);
            if (parts.isEmpty()) {
                continue;
            }
            if (first && !body.isEmpty()) {
                body.add("");
            }
            first = false;
            body.addAll(wrap("@param " + param[0] + " " + String.join(" <p>", parts), indent, "    "));
        }
        if (body.isEmpty()) {
            return List.of();
        }
        List<String> lines = new ArrayList<>();
        lines.add(indent + "/**");
        for (String line : body) {
            lines.add(line.isEmpty() ? indent + " *" : indent + " * " + line);
        }
        lines.add(indent + " */");
        return lines;
    }

    private static List<String> paragraphs(String text) {
        List<String> result = new ArrayList<>();
        if (text == null) {
            return result;
        }
        for (String paragraph : text.strip().split("\\n\\s*\\n")) {
            String joined = String.join(" ", paragraph.strip().split("\\s+"));
            if (!joined.isEmpty()) {
                result.add(escapeHtml(joined));
            }
        }
        return result;
    }

    private static String escapeHtml(String text) {
        return text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("@", "&#64;")
                .replace("*/", "*&#47;");
    }

    private static List<String> wrap(String text, String indent, String continuation) {
        int width = 100 - indent.length() - 3;
        List<String> lines = new ArrayList<>();
        StringBuilder line = new StringBuilder();
        for (String word : text.split(" ")) {
            if (word.isEmpty()) {
                continue;
            }
            if (line.length() > 0 && line.length() + 1 + word.length() > width) {
                lines.add(line.toString());
                line = new StringBuilder(continuation);
            } else if (line.length() > 0 && !line.toString().equals(continuation)) {
                line.append(' ');
            }
            line.append(word);
        }
        if (line.length() > 0) {
            lines.add(line.toString());
        }
        return lines;
    }

    private static List<String[]> fieldParams(List<FieldPlan> fields) {
        List<String[]> params = new ArrayList<>();
        for (FieldPlan field : fields) {
            params.add(new String[] {field.name(), field.description()});
        }
        return params;
    }

    private static boolean nullable(String annotation) {
        return annotation.equals("Void") || annotation.equals("Object");
    }

    private static String components(List<FieldPlan> fields) {
        List<String> parts = new ArrayList<>();
        for (FieldPlan field : fields) {
            parts.add(field.annotation() + " " + field.name());
        }
        return String.join(", ", parts);
    }

    private static void requireChecks(List<String> lines, List<FieldPlan> fields, String indent, String name) {
        List<FieldPlan> checked = new ArrayList<>();
        for (FieldPlan field : fields) {
            if (field.required() && !nullable(field.annotation())) {
                checked.add(field);
            }
        }
        if (checked.isEmpty()) {
            return;
        }
        lines.add("");
        lines.add(indent + "    public " + name + " {");
        for (FieldPlan field : checked) {
            lines.add(indent + "        Objects.requireNonNull(" + field.name() + ", " + literal(field.name()) + ");");
        }
        lines.add(indent + "    }");
    }

    private static List<String> recordBody(
            String name, List<FieldPlan> fields, String implement, String markerMethod, String indent, String description) {
        List<String> lines = new ArrayList<>(javadoc(description, fieldParams(fields), indent));
        String modifier = indent.isEmpty() ? "public " : "";
        String declaration = indent + modifier + "record " + name + "(" + components(fields) + ")" + implement;
        boolean checks = fields.stream().anyMatch(field -> field.required() && !nullable(field.annotation()));
        if (!checks && markerMethod == null) {
            lines.add(declaration + " {}");
            return lines;
        }
        lines.add(declaration + " {");
        List<String> body = new ArrayList<>();
        requireChecks(body, fields, indent, name);
        if (markerMethod != null) {
            body.add("");
            body.add(indent + "    @Override");
            body.add(indent + "    public void " + markerMethod + "() {}");
        }
        if (!body.isEmpty() && body.get(0).isEmpty()) {
            body.remove(0);
        }
        lines.addAll(body);
        lines.add(indent + "}");
        return lines;
    }

    private static String file(EmissionPlan plan, JsonNode model, String name, List<String> body, List<String> extraImports) {
        TreeSet<String> imports = new TreeSet<>(extraImports);
        String text = String.join("\n", body);
        if (text.contains("List<")) {
            imports.add("java.util.List");
        }
        if (text.contains("Map<")) {
            imports.add("java.util.Map");
        }
        if (text.contains("Objects.")) {
            imports.add("java.util.Objects");
        }
        List<String> lines = new ArrayList<>();
        lines.add("// " + Metadata.header(model));
        lines.add("");
        lines.add("package " + plan.target().javaPackage() + ";");
        lines.add("");
        if (!imports.isEmpty()) {
            for (String item : imports) {
                lines.add("import " + item + ";");
            }
            lines.add("");
        }
        lines.addAll(body);
        lines.add("");
        return String.join("\n", lines);
    }

    private static String implementsClause(TypePlan item, List<String> imports) {
        if (item.marker() == null) {
            return "";
        }
        if (item.marker().providerPackage() != null) {
            imports.add(item.marker().providerPackage().javaPackage() + "." + item.marker().protocol());
        }
        return " implements " + item.marker().protocol();
    }

    private static String renderType(EmissionPlan plan, JsonNode model, TypePlan item) {
        List<String> imports = new ArrayList<>();
        List<String> body = new ArrayList<>();
        if (item.kind() == Kind.ENUM) {
            body.addAll(javadoc(item.description(), List.of(), ""));
            body.add("public enum " + item.name() + " {");
            for (int index = 0; index < item.members().size(); index += 1) {
                Plan.Member member = item.members().get(index);
                body.addAll(javadoc(member.description(), List.of(), "    "));
                body.add(
                        "    "
                                + member.name()
                                + "("
                                + literal(member.value())
                                + ")"
                                + (index + 1 < item.members().size() ? "," : ";"));
            }
            body.add("");
            body.add("    private final String value;");
            body.add("");
            body.add("    " + item.name() + "(String value) {");
            body.add("        this.value = value;");
            body.add("    }");
            body.add("");
            body.add("    public String value() {");
            body.add("        return value;");
            body.add("    }");
            body.add("}");
        } else if (item.kind() == Kind.ALIAS) {
            String kind = item.expression().kind();
            if (kind.equals("union")) {
                body.addAll(javadoc(item.description(), List.of(), ""));
                body.add("public sealed interface " + item.name() + " {");
                for (int index = 0; index < item.expression().arguments().size(); index += 1) {
                    String annotation = Plan.annotate(item.expression().arguments().get(index), plan.names());
                    List<FieldPlan> single =
                            List.of(new FieldPlan("", "value", "value", item.expression().arguments().get(index), annotation, true));
                    if (index > 0) {
                        body.add("");
                    }
                    String variant = "Variant" + (index + 1);
                    body.addAll(recordBody(variant, single, " implements " + item.name(), null, "    ", ""));
                }
                body.add("}");
            } else {
                String annotation = Plan.annotate(item.expression(), plan.names());
                List<FieldPlan> single = List.of(new FieldPlan("", "value", "value", item.expression(), annotation, true));
                body.addAll(recordBody(item.name(), single, "", null, "", item.description()));
            }
        } else {
            String implement = implementsClause(item, imports);
            body.addAll(
                    recordBody(
                            item.name(),
                            item.fields(),
                            implement,
                            item.marker() == null ? null : item.marker().method(),
                            "",
                            item.description()));
        }
        return file(plan, model, item.name(), body, imports);
    }

    private static String renderProtocol(EmissionPlan plan, JsonNode model, DeclarationPlan declaration) {
        List<String> body =
                List.of(
                        "public interface " + declaration.protocol() + " {",
                        "    void " + declaration.method() + "();",
                        "}");
        return file(plan, model, declaration.protocol(), body, List.of());
    }

    private static String renderDeclaration(EmissionPlan plan, JsonNode model) {
        List<String> body =
                List.of("public final class " + Plan.DECLARATION_CLASS + " {", "    Declaration() {}", "}");
        return file(plan, model, Plan.DECLARATION_CLASS, body, List.of());
    }

    private static String renderFunctions(EmissionPlan plan, JsonNode model) {
        List<String> body = new ArrayList<>();
        if (plan.packageDescription() != null && !plan.packageDescription().isBlank()) {
            body.addAll(javadoc(
                    "Runtime Conditions defined by " + plan.target().rootExtension() + ".\n\n" + plan.packageDescription(),
                    List.of(),
                    ""));
        }
        body.add("public final class " + Plan.FUNCTIONS_CLASS + " {");
        body.add("    private " + Plan.FUNCTIONS_CLASS + "() {}");
        List<DeclarationPlan> ordered = new ArrayList<>(plan.declarations());
        ordered.sort(Comparator.comparing(DeclarationPlan::function));
        for (DeclarationPlan declaration : ordered) {
            body.add("");
            if (declaration.description() != null && !declaration.description().isBlank()) {
                body.addAll(javadoc(
                        "Declares a " + declaration.kind() + " Condition.\n\n" + declaration.description(),
                        List.<String[]>of(new String[] {"fields", "the fields of the Condition"}),
                        "    "));
            }
            body.add(
                    "    public static "
                            + Plan.DECLARATION_CLASS
                            + " "
                            + declaration.function()
                            + "("
                            + declaration.protocol()
                            + "... fields) {");
            body.add("        Objects.requireNonNull(fields, \"fields\");");
            body.add("        return new " + Plan.DECLARATION_CLASS + "();");
            body.add("    }");
        }
        body.add("}");
        return file(plan, model, Plan.FUNCTIONS_CLASS, body, List.of());
    }

    public static Map<String, String> renderSources(EmissionPlan plan, JsonNode model) {
        if (!model.path("metadata").path("semanticSha256").asText().equals(plan.modelDigest())
                || !model.path("rootExtension").path("id").asText().equals(plan.target().rootExtension())) {
            throw fail("model", "RCJ1003", "model", "emission plan does not match supplied model");
        }
        Map<String, String> sources = new TreeMap<>();
        CURRENT_EXAMPLES.set(Examples.render(plan, model));
        try {
            if (!plan.declarations().isEmpty()) {
                sources.put(sourcePath(plan, Plan.DECLARATION_CLASS), renderDeclaration(plan, model));
                sources.put(sourcePath(plan, Plan.FUNCTIONS_CLASS), renderFunctions(plan, model));
            }
            for (DeclarationPlan declaration : plan.declarations()) {
                sources.put(sourcePath(plan, declaration.protocol()), renderProtocol(plan, model, declaration));
            }
            for (TypePlan item : plan.types()) {
                sources.put(sourcePath(plan, item.name()), renderType(plan, model, item));
            }
        } finally {
            CURRENT_EXAMPLES.remove();
        }
        return sources;
    }

    private static List<String> markdown(String text, EmissionPlan plan, Map<String, Examples.Rendered> examples) {
        List<String> lines = new ArrayList<>();
        for (Examples.Segment segment : Examples.split(text.strip())) {
            if (!lines.isEmpty()) {
                lines.add("");
            }
            if (segment.condition() == null) {
                lines.add(segment.text());
                continue;
            }
            Examples.Rendered rendered = examples.get(segment.source());
            if (rendered != null) {
                lines.add("```java");
                for (String name : rendered.imports()) {
                    lines.add("import " + (name.contains(".") ? name : plan.target().javaPackage() + "." + name) + ";");
                }
                lines.add("");
                lines.add(rendered.code());
                lines.add("```");
                lines.add("");
                lines.add("In a Profile, this declares:");
                lines.add("");
            }
            lines.add("```yaml");
            lines.add(Examples.dedent(segment.source()).strip());
            lines.add("```");
        }
        return lines;
    }

    /** The package README from the extension's documentation, or null when undocumented. */
    static String readme(EmissionPlan plan, JsonNode model) {
        if (plan.packageDescription() == null || plan.packageDescription().isBlank()) {
            return null;
        }
        Map<String, Examples.Rendered> examples = Examples.render(plan, model);
        PackageTarget target = plan.target();
        List<String> lines = new ArrayList<>();
        lines.add("<!-- " + Metadata.header(model) + " -->");
        lines.add("");
        lines.add("# " + target.groupId() + ":" + target.artifactId());
        lines.add("");
        lines.addAll(markdown(plan.packageDescription(), plan, examples));
        lines.add("");
        lines.add("```xml");
        lines.add("<dependency>");
        lines.add("  <groupId>" + target.groupId() + "</groupId>");
        lines.add("  <artifactId>" + target.artifactId() + "</artifactId>");
        lines.add("  <version>" + target.version() + "</version>");
        lines.add("</dependency>");
        lines.add("```");
        lines.add("");
        lines.add("Runtime Conditions extension: " + target.rootExtension());
        List<DeclarationPlan> ordered = new ArrayList<>(plan.declarations());
        ordered.sort(Comparator.comparing(DeclarationPlan::function));
        for (DeclarationPlan declaration : ordered) {
            lines.add("");
            lines.add("## " + Plan.FUNCTIONS_CLASS + "." + declaration.function());
            lines.add("");
            lines.add("`" + Plan.FUNCTIONS_CLASS + "." + declaration.function() + "` declares a `" + declaration.kind() + "` Condition.");
            if (declaration.description() != null && !declaration.description().isBlank()) {
                lines.add("");
                lines.addAll(markdown(declaration.description(), plan, examples));
            }
        }
        // Fields this package adds to Conditions; fields sharing one description share a section.
        Map<String, List<Examples.FieldDoc>> groups = new java.util.LinkedHashMap<>();
        for (Examples.FieldDoc field : Examples.conditionFields(plan, model)) {
            groups.computeIfAbsent(field.description(), key -> new ArrayList<>()).add(field);
        }
        for (Map.Entry<String, List<Examples.FieldDoc>> group : groups.entrySet()) {
            List<String> names = new ArrayList<>();
            for (Examples.FieldDoc field : group.getValue()) {
                names.add(field.name());
            }
            lines.add("");
            lines.add("## " + String.join(", ", names));
            lines.add("");
            for (Examples.FieldDoc field : group.getValue()) {
                String scope = field.interfaceType().isEmpty() ? "" : " with interface type `" + field.interfaceType() + "`";
                lines.add("`" + field.name() + "` sets the `" + field.path() + "` field of `" + field.kind() + "` Conditions" + scope + ".");
            }
            lines.add("");
            lines.addAll(markdown(group.getKey(), plan, examples));
        }
        lines.add("");
        lines.add("Every record, component and enum constant is documented in the Javadoc.");
        lines.add("");
        return String.join("\n", lines);
    }

    public static Map<String, String> renderPackage(EmissionPlan plan, JsonNode model) {
        Map<String, String> files = new TreeMap<>(renderSources(plan, model));
        files.putAll(Metadata.renderResources(plan, model));
        // Only documented extensions get a README, so other packages are unchanged.
        String readme = readme(plan, model);
        if (readme != null) {
            files.put("README.md", readme);
        }
        return files;
    }

    public static List<String> writeFiles(Map<String, String> files, EmissionPlan plan, Path output) {
        String key = plan.target().packageKey();
        boolean occupied;
        try {
            occupied =
                    Files.exists(output, LinkOption.NOFOLLOW_LINKS)
                            && (Files.isSymbolicLink(output)
                                    || !Files.isDirectory(output, LinkOption.NOFOLLOW_LINKS)
                                    || hasEntries(output));
        } catch (IOException error) {
            throw fail("output", "RCJ3002", key, "cannot inspect output directory");
        }
        if (occupied) {
            throw fail("output", "RCJ3001", key, "output directory must be new or empty and not a symbolic link");
        }
        try {
            Files.createDirectories(output);
            for (Map.Entry<String, String> entry : new TreeMap<>(files).entrySet()) {
                Path destination = output.resolve(entry.getKey());
                Files.createDirectories(destination.getParent());
                Files.writeString(destination, entry.getValue(), StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
            }
        } catch (FileAlreadyExistsException error) {
            throw fail("output", "RCJ3001", key, "output directory must be new or empty and not a symbolic link");
        } catch (IOException error) {
            throw fail("output", "RCJ3002", key, "cannot write generated package files");
        }
        return new ArrayList<>(new TreeMap<>(files).keySet());
    }

    private static boolean hasEntries(Path directory) throws IOException {
        try (Stream<Path> entries = Files.list(directory)) {
            return entries.findAny().isPresent();
        }
    }

    public static List<String> emitPackage(EmissionPlan plan, JsonNode model, Path output) {
        return writeFiles(renderPackage(plan, model), plan, output);
    }
}
