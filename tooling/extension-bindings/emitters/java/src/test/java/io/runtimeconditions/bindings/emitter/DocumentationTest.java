package io.runtimeconditions.bindings.emitter;

import static io.runtimeconditions.bindings.emitter.TestSupport.buildCase;
import static io.runtimeconditions.bindings.emitter.TestSupport.delete;
import static io.runtimeconditions.bindings.emitter.TestSupport.temporaryDirectory;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.runtimeconditions.bindings.emitter.TestSupport.Built;
import java.io.IOException;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Stream;
import javax.tools.DocumentationTool;
import javax.tools.JavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;
import org.junit.jupiter.api.Test;

/** Extension descriptions become Javadoc that the javadoc tool accepts. */
class DocumentationTest {
    private static final String CASE = "14-documented-extension";

    @Test
    void descriptionsBecomeJavadoc() throws IOException {
        Path workspace = temporaryDirectory();
        try {
            Built built = buildCase(CASE, workspace, null);
            Path sources = built.directory().resolve(Source.sourceRoot(built.plan()));
            String bindings = Files.readString(sources.resolve("Bindings.java"));
            String keyValue = Files.readString(sources.resolve("KeyValue.java"));
            String engine = Files.readString(sources.resolve("KeyValueEngine.java"));
            String spec = Files.readString(sources.resolve("Spec.java"));

            // Extension summary on the Bindings class, kind description on the function.
            assertTrue(bindings.contains("Common integrations a workload depends on"), bindings);
            assertTrue(bindings.contains("The workload keeps frequently used data in a cache"), bindings);
            // Interface type description on the record, field meaning and rule as @param.
            assertTrue(keyValue.contains(" * A key/value store, such as Redis or Memcached."), keyValue);
            assertTrue(keyValue.contains("@param engine The cache engine the workload needs."), keyValue);
            // Field value description on the enum constant.
            assertTrue(engine.contains("/**\n     * Redis, an in-memory key/value store.\n     */\n    REDIS("), engine);
            // Comparison operators in a JSON Schema rule are HTML-escaped.
            assertTrue(spec.contains("&gt;=1.2.0"), spec);

            // Maven Central shows the pom description: the extension summary.
            String pom = Files.readString(built.directory().resolve("pom.xml"));
            assertTrue(pom.contains("<description>Common integrations a workload depends on: network APIs"), pom);

            assertEquals("", runJavadoc(sources, workspace.resolve("javadoc")));
        } finally {
            delete(workspace);
        }
    }

    @Test
    void readmeExamplesCompileAgainstTheGeneratedPackage() throws IOException {
        Path workspace = temporaryDirectory();
        try {
            Built built = buildCase(CASE, workspace, null);
            Path sources = built.directory().resolve(Source.sourceRoot(built.plan()));
            String readme = Files.readString(built.directory().resolve("README.md"));
            java.util.regex.Matcher matcher =
                    java.util.regex.Pattern.compile("```java\n(.*?)```", java.util.regex.Pattern.DOTALL).matcher(readme);
            List<Path> files = new ArrayList<>();
            try (Stream<Path> walk = Files.walk(sources)) {
                walk.filter(path -> path.toString().endsWith(".java")).sorted().forEach(files::add);
            }
            Path examples = workspace.resolve("examples/examples");
            Files.createDirectories(examples);
            int index = 0;
            while (matcher.find()) {
                StringBuilder imports = new StringBuilder();
                StringBuilder statements = new StringBuilder();
                for (String line : matcher.group(1).split("\n")) {
                    (line.startsWith("import ") ? imports : statements).append(line).append('\n');
                }
                Path file = examples.resolve("Example" + index + ".java");
                Files.writeString(file, "package examples;\n\n" + imports + "\nclass Example" + index
                        + " {\n    static Object run() {\n        return " + statements.toString().strip().replaceAll(";$", "")
                        + ";\n    }\n}\n");
                files.add(file);
                index += 1;
            }
            assertTrue(index >= 5, "expected the fixture's examples, found " + index + "\n" + readme);
            javax.tools.JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
            StringWriter errors = new StringWriter();
            try (StandardJavaFileManager manager = compiler.getStandardFileManager(null, null, StandardCharsets.UTF_8)) {
                boolean ok = compiler.getTask(
                                errors,
                                manager,
                                null,
                                List.of("-d", workspace.resolve("classes").toString()),
                                null,
                                manager.getJavaFileObjectsFromPaths(files))
                        .call();
                assertTrue(ok, errors.toString());
            }
            // The same code is in the Javadoc of the Bindings method.
            String bindings = Files.readString(sources.resolve("Bindings.java"));
            assertTrue(bindings.contains("<pre>{@code"), bindings);
            assertTrue(bindings.contains("Bindings.cache(new KeyValue(KeyValueEngine.REDIS));"), bindings);
        } finally {
            delete(workspace);
        }
    }

    @Test
    void dependentReadmeExamplesCallTheDependencyAndCompileAgainstBothPackages() throws IOException {
        Path workspace = temporaryDirectory();
        try {
            // The dependency is case 14's extension, so its own package is case 14's.
            Built dependency = buildCase(CASE, workspace, null);
            String name = "15-documented-dependent";
            com.fasterxml.jackson.databind.node.ObjectNode model = Model.load(TestSupport.modelPath(name));
            Target.PackageTarget base = TestSupport.targetFor(name, model, java.util.Map.of());
            Target.PackageTarget target = new Target.PackageTarget(
                    base.packageKey(), base.rootExtension(), base.groupId(), base.artifactId(), base.javaPackage(),
                    base.version(), base.sourceDirectory(), base.minimumJavaVersion(), base.publicationMode(),
                    base.repositoryUrl(), base.registryId(),
                    List.of(new Target.Dependency(
                            dependency.plan().target().rootExtension(), dependency.plan().target().groupId(),
                            dependency.plan().target().artifactId(), dependency.plan().target().javaPackage(), "1.0.0")),
                    base.emitterSha256());
            Plan.EmissionPlan plan = Plan.build(model, target);

            // Names planned from the dependent model match the dependency package's own.
            Plan.EmissionPlan derived = Dependencies.plan(plan, model, dependency.plan().target().rootExtension());
            assertTrue(derived != null, "the dependency package could not be planned from the dependent model");
            assertEquals(exported(dependency.plan()), exported(derived));

            Path directory = workspace.resolve("pkg").resolve(name);
            Source.emitPackage(plan, model, directory);
            String readme = Files.readString(directory.resolve("README.md"));
            for (String expected : List.of(
                    "## KeyValueConfiguration",
                    "`KeyValueConfiguration` sets the `configuration` field of `cache` Conditions with interface type `key_value`.",
                    "import " + dependency.plan().target().javaPackage() + ".Bindings;",
                    "import " + plan.target().javaPackage() + ".KeyValueConfiguration;",
                    "Bindings.cache(",
                    "In a Profile, this declares:")) {
                assertTrue(readme.contains(expected), expected + "\n" + readme);
            }
            java.util.regex.Matcher matcher =
                    java.util.regex.Pattern.compile("```java\n(.*?)```", java.util.regex.Pattern.DOTALL).matcher(readme);
            List<Path> files = new ArrayList<>();
            for (Path root : List.of(
                    dependency.directory().resolve(Source.sourceRoot(dependency.plan())),
                    directory.resolve(Source.sourceRoot(plan)))) {
                try (Stream<Path> walk = Files.walk(root)) {
                    walk.filter(path -> path.toString().endsWith(".java")).sorted().forEach(files::add);
                }
            }
            Path examples = workspace.resolve("examples/examples");
            Files.createDirectories(examples);
            int index = 0;
            while (matcher.find()) {
                StringBuilder imports = new StringBuilder();
                StringBuilder statements = new StringBuilder();
                for (String line : matcher.group(1).split("\n")) {
                    (line.startsWith("import ") ? imports : statements).append(line).append('\n');
                }
                Path file = examples.resolve("Example" + index + ".java");
                Files.writeString(file, "package examples;\n\n" + imports + "\nclass Example" + index
                        + " {\n    static Object run() {\n        return " + statements.toString().strip().replaceAll(";$", "")
                        + ";\n    }\n}\n");
                files.add(file);
                index += 1;
            }
            assertTrue(index >= 2, "expected the fixture's examples, found " + index + "\n" + readme);
            javax.tools.JavaCompiler compiler = ToolProvider.getSystemJavaCompiler();
            StringWriter errors = new StringWriter();
            try (StandardJavaFileManager manager = compiler.getStandardFileManager(null, null, StandardCharsets.UTF_8)) {
                boolean ok = compiler.getTask(
                                errors,
                                manager,
                                null,
                                List.of("-d", workspace.resolve("classes").toString()),
                                null,
                                manager.getJavaFileObjectsFromPaths(files))
                        .call();
                assertTrue(ok, errors.toString());
            }
            // The condition field's Javadoc shows the same code.
            String configuration = Files.readString(
                    directory.resolve(Source.sourceRoot(plan)).resolve("KeyValueConfiguration.java"));
            assertTrue(configuration.contains("<pre>{@code"), configuration);
            assertTrue(configuration.contains("Bindings.cache("), configuration);
        } finally {
            delete(workspace);
        }
    }

    private static List<String> exported(Plan.EmissionPlan plan) {
        List<String> names = new ArrayList<>();
        for (Plan.TypePlan type : plan.types()) {
            names.add("type " + type.name());
        }
        for (Plan.DeclarationPlan declaration : plan.declarations()) {
            names.add("function " + declaration.function());
        }
        names.sort(null);
        return names;
    }

    /** Runs the JDK javadoc tool with HTML and syntax checks and returns its errors. */
    private static String runJavadoc(Path sources, Path output) throws IOException {
        DocumentationTool tool = ToolProvider.getSystemDocumentationTool();
        List<Path> files = new ArrayList<>();
        try (Stream<Path> walk = Files.walk(sources)) {
            walk.filter(path -> path.toString().endsWith(".java")).sorted().forEach(files::add);
        }
        StringWriter errors = new StringWriter();
        try (StandardJavaFileManager manager = tool.getStandardFileManager(null, null, StandardCharsets.UTF_8)) {
            Iterable<? extends JavaFileObject> units = manager.getJavaFileObjectsFromPaths(files);
            Files.createDirectories(output);
            boolean ok = tool.getTask(
                            errors,
                            manager,
                            null,
                            null,
                            List.of("-quiet", "-Xdoclint:html,syntax", "-d", output.toString()),
                            units)
                    .call();
            return ok ? "" : errors.toString();
        }
    }
}
