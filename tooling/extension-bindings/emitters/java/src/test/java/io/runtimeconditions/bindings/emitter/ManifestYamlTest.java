package io.runtimeconditions.bindings.emitter;

import static io.runtimeconditions.bindings.emitter.TestSupport.TOOLING;
import static io.runtimeconditions.bindings.emitter.TestSupport.developmentTarget;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.fasterxml.jackson.databind.node.ObjectNode;
import io.runtimeconditions.bindings.emitter.Plan.EmissionPlan;
import io.runtimeconditions.bindings.emitter.Target.PackageTarget;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.snakeyaml.engine.v2.api.Load;
import org.snakeyaml.engine.v2.api.LoadSettings;
import org.snakeyaml.engine.v2.schema.CoreSchema;

/** The manifest is read by parsers of every language, so it must mean the same under the YAML 1.2 core schema. */
class ManifestYamlTest {
    private static final Path COMMON = TOOLING.resolve("../../bindings/common-integrations/go/runtimeconditions.binding-model.yaml").normalize();

    @Test
    void everyNativeNameIsAStringUnderTheYamlCoreSchemaEvenWhenItSpellsNull() throws Exception {
        assumeTrue(Files.exists(COMMON));
        ObjectNode model = Model.load(COMMON);
        PackageTarget base = developmentTarget();
        PackageTarget target =
                new PackageTarget(
                        "common-integrations",
                        model.path("rootExtension").path("id").asText(),
                        "io.runtimeconditions",
                        "common-integrations",
                        "io.runtimeconditions.commonintegrations",
                        base.version(),
                        "bindings/common-integrations/java",
                        base.minimumJavaVersion(),
                        base.publicationMode(),
                        base.repositoryUrl(),
                        base.registryId(),
                        List.of(),
                        base.emitterSha256());
        EmissionPlan plan = Plan.build(model, target);
        String text = Metadata.renderResources(plan, model).get(Metadata.MANIFEST_PATH);

        Object loaded = new Load(LoadSettings.builder().setSchema(new CoreSchema()).build()).loadFromString(text);
        Map<?, ?> manifest = assertInstanceOf(Map.class, loaded);
        int checked = 0;
        for (Object type : (List<?>) manifest.get("types")) {
            Map<?, ?> entry = (Map<?, ?>) type;
            assertInstanceOf(String.class, entry.get("nativeName"), String.valueOf(entry));
            for (Object member : entry.containsKey("members") ? (List<?>) entry.get("members") : List.of()) {
                Map<?, ?> item = (Map<?, ?>) member;
                assertInstanceOf(String.class, item.get("nativeName"), String.valueOf(item));
                assertInstanceOf(String.class, item.get("value"), String.valueOf(item));
                checked += 1;
            }
        }
        assertTrue(checked > 0);
    }
}
