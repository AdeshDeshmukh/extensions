package io.runtimeconditions.bindings.emitter;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.runtimeconditions.bindings.emitter.Plan.EmissionPlan;
import io.runtimeconditions.bindings.emitter.Target.Dependency;
import io.runtimeconditions.bindings.emitter.Target.PackageTarget;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Plans a direct dependency's package from a dependent package's binding model.
 *
 * <p>A package that adds fields to a dependency's kinds, such as env-configuration adding
 * configuration to common-integrations' cache, has examples that call the dependency's declaration
 * function with the dependency's interface types. The binding model already carries the
 * dependency's vocabulary and schemas, and {@link Plan#build} keeps only what the root extension
 * owns, so planning the same model with the dependency as root yields the dependency package's
 * names.
 */
final class Dependencies {
    private Dependencies() {}

    /** Narrows a binding model to the closure of one of its extensions, with it as root. */
    static JsonNode model(JsonNode model, String extension) {
        Map<String, JsonNode> byId = new HashMap<>();
        for (JsonNode item : Json.list(model.path("extensions"))) {
            byId.put(item.path("id").asText(), item);
        }
        JsonNode root = byId.get(extension);
        if (root == null) {
            return null;
        }
        Set<String> closure = new LinkedHashSet<>();
        List<String> pending = new ArrayList<>(List.of(extension));
        while (!pending.isEmpty()) {
            String id = pending.remove(pending.size() - 1);
            JsonNode item = byId.get(id);
            if (item == null || !closure.add(id)) {
                continue;
            }
            for (JsonNode dependency : Json.list(item.path("dependencies"))) {
                pending.add(dependency.asText());
            }
        }
        ObjectNode narrowed = model.deepCopy();
        ObjectNode identity = Json.MAPPER.createObjectNode();
        for (String key : List.of("id", "version", "semanticSha256")) {
            if (root.has(key)) {
                identity.set(key, root.get(key));
            }
        }
        narrowed.set("rootExtension", identity);
        narrowed.set("extensions", filter(model.path("extensions"), item -> closure.contains(item.path("id").asText())));
        ObjectNode vocabulary = model.path("vocabulary").deepCopy();
        for (String key : List.of("owners", "interfaces", "conditionFields", "interfaceFields", "valueDomains")) {
            if (vocabulary.has(key)) {
                vocabulary.set(key, filter(vocabulary.path(key), item -> closure.contains(item.path("owner").asText())));
            }
        }
        ArrayNode owned = Json.MAPPER.createArrayNode();
        ArrayNode imported = Json.MAPPER.createArrayNode();
        for (String key : List.of("ownedDeclarations", "importedDeclarations")) {
            for (JsonNode item : Json.list(model.path("vocabulary").path(key))) {
                String owner = item.path("owner").asText();
                if (owner.equals(extension)) {
                    owned.add(item);
                } else if (closure.contains(owner)) {
                    imported.add(item);
                }
            }
        }
        vocabulary.set("ownedDeclarations", owned);
        vocabulary.set("importedDeclarations", imported);
        narrowed.set("vocabulary", vocabulary);
        narrowed.set("schemas", filter(model.path("schemas"), item -> closure.contains(item.path("owner").asText())));
        return narrowed;
    }

    /** Plans the package of a direct dependency extension, or returns null when it cannot. */
    static EmissionPlan plan(EmissionPlan plan, JsonNode model, String extension) {
        Dependency dependency = null;
        for (Dependency item : plan.target().dependencies()) {
            if (item.extension().equals(extension)) {
                dependency = item;
            }
        }
        JsonNode narrowed = model(model, extension);
        if (dependency == null || narrowed == null) {
            return null;
        }
        // The dependency's own providers only name its imports, which examples never use.
        List<Dependency> direct = new ArrayList<>();
        for (JsonNode item : Json.list(narrowed.path("extensions"))) {
            if (item.path("id").asText().equals(extension)) {
                int index = 0;
                for (JsonNode transitive : Json.list(item.path("dependencies"))) {
                    direct.add(new Dependency(transitive.asText(), "unresolved", "unresolved-" + index,
                            "unresolved.p" + index, "0.0.0"));
                    index += 1;
                }
            }
        }
        PackageTarget target = plan.target();
        PackageTarget dependencyTarget = new PackageTarget(
                dependency.artifactId(),
                extension,
                dependency.groupId(),
                dependency.artifactId(),
                dependency.javaPackage(),
                dependency.version(),
                target.sourceDirectory(),
                target.minimumJavaVersion(),
                target.publicationMode(),
                target.repositoryUrl(),
                target.registryId(),
                direct,
                target.emitterSha256());
        try {
            return Plan.build(narrowed, dependencyTarget);
        } catch (DiagnosticError unplannable) {
            return null;
        }
    }

    private static ArrayNode filter(JsonNode items, java.util.function.Predicate<JsonNode> keep) {
        ArrayNode result = Json.MAPPER.createArrayNode();
        for (JsonNode item : Json.list(items)) {
            if (keep.test(item)) {
                result.add(item);
            }
        }
        return result;
    }
}
