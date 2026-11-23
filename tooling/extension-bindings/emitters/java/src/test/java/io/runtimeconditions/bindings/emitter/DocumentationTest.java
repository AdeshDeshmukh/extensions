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

            assertEquals("", runJavadoc(sources, workspace.resolve("javadoc")));
        } finally {
            delete(workspace);
        }
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
