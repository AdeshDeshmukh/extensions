package io.runtimeconditions.bindings.emitter;

import static io.runtimeconditions.bindings.emitter.TestSupport.TOOLING;
import static io.runtimeconditions.bindings.emitter.TestSupport.delete;
import static io.runtimeconditions.bindings.emitter.TestSupport.developmentTarget;
import static io.runtimeconditions.bindings.emitter.TestSupport.temporaryDirectory;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import com.fasterxml.jackson.databind.node.ObjectNode;
import io.runtimeconditions.bindings.emitter.Plan.EmissionPlan;
import io.runtimeconditions.bindings.emitter.Target.Dependency;
import io.runtimeconditions.bindings.emitter.Target.PackageTarget;
import io.runtimeconditions.bindings.emitter.TestSupport.Compiled;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;

class RealModelsTest {
    private static final Path COMMON = TOOLING.resolve("../../bindings/common-integrations/go/runtimeconditions.binding-model.yaml").normalize();
    private static final Path ENV = TOOLING.resolve("../../bindings/env-configuration/go/runtimeconditions.binding-model.yaml").normalize();

    private static PackageTarget target(String key, String javaPackage, ObjectNode model, List<Dependency> dependencies) {
        PackageTarget base = developmentTarget();
        return new PackageTarget(
                key,
                model.path("rootExtension").path("id").asText(),
                "io.runtimeconditions",
                key,
                javaPackage,
                base.version(),
                "bindings/" + key + "/java",
                base.minimumJavaVersion(),
                base.publicationMode(),
                base.repositoryUrl(),
                base.registryId(),
                dependencies,
                base.emitterSha256());
    }

    @Test
    void theCommittedFirstPartyModelsEmitPackagesThatCompileIncludingRecursiveMaps() throws Exception {
        assumeTrue(Files.exists(COMMON) && Files.exists(ENV));
        Path workspace = temporaryDirectory();
        try {
            ObjectNode common = Model.load(COMMON);
            EmissionPlan commonPlan = Plan.build(common, target("common-integrations", "io.runtimeconditions.commonintegrations", common, List.of()));
            Path commonDirectory = workspace.resolve("common");
            Source.emitPackage(commonPlan, common, commonDirectory);
            Verification.verifyPackage(commonPlan, commonDirectory, common);
            Files.writeString(commonDirectory.resolve(Source.sourcePath(commonPlan, "Exercise")), Conformance.source(commonPlan, common));
            Compiled compiled = TestSupport.compile(List.of(commonDirectory.resolve("src/main/java")), workspace.resolve("common-classes"));
            assertTrue(compiled.success(), compiled.output());

            ObjectNode env = Model.load(ENV);
            EmissionPlan envPlan =
                    Plan.build(
                            env,
                            target(
                                    "env-configuration",
                                    "io.runtimeconditions.envconfiguration",
                                    env,
                                    List.of(new Dependency(common.path("rootExtension").path("id").asText(), "io.runtimeconditions", "common-integrations", "io.runtimeconditions.commonintegrations", "0.1.0"))));
            Path envDirectory = workspace.resolve("env");
            Source.emitPackage(envPlan, env, envDirectory);
            Verification.verifyPackage(envPlan, envDirectory, env);
            Files.writeString(envDirectory.resolve(Source.sourcePath(envPlan, "Exercise")), Conformance.source(envPlan, env));
            Compiled envCompiled =
                    TestSupport.compile(
                            List.of(envDirectory.resolve("src/main/java"), commonDirectory.resolve("src/main/java")), workspace.resolve("env-classes"));
            assertTrue(envCompiled.success(), envCompiled.output());
        } finally {
            delete(workspace);
        }
    }

    /**
     * The other first-party extensions have no dependencies. Two of them name a property "optional", which asks for a type
     * named Optional, a name the generated source must not take from java.util.
     */
    @Test
    void theOtherFirstPartyModelsEmitPackagesThatCompileIncludingPropertiesNamedLikeJavaTypes() throws Exception {
        List<String> names = List.of("aws-s3", "google-analytics", "kubernetes-api", "nats-service", "source-control");
        Path workspace = temporaryDirectory();
        try {
            for (String name : names) {
                Path path = TOOLING.resolve("../../bindings/" + name + "/go/runtimeconditions.binding-model.yaml").normalize();
                assumeTrue(Files.exists(path));
                ObjectNode model = Model.load(path);
                EmissionPlan plan = Plan.build(model, target(name, "io.runtimeconditions." + name.replace("-", ""), model, List.of()));
                Path directory = workspace.resolve(name);
                Source.emitPackage(plan, model, directory);
                Verification.verifyPackage(plan, directory, model);
                Files.writeString(directory.resolve(Source.sourcePath(plan, "Exercise")), Conformance.source(plan, model));
                Compiled compiled = TestSupport.compile(List.of(directory.resolve("src/main/java")), workspace.resolve(name + "-classes"));
                assertTrue(compiled.success(), name + ": " + compiled.output());
            }
        } finally {
            delete(workspace);
        }
    }
}
