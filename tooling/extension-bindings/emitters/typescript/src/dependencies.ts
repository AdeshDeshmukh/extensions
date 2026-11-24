// A package that adds fields to a dependency's kinds, such as env-configuration
// adding configuration to common-integrations' cache, has examples that call the
// dependency's declaration function with the dependency's interface types. The
// binding model already carries the dependency's vocabulary and schemas, and
// buildPlan keeps only what the root extension owns, so planning the same model
// with the dependency as root yields the dependency package's names.

import type { Model } from "./model.js";
import { buildPlan, type EmissionPlan } from "./plan.js";
import type { PackageTarget } from "./target.js";

/** Narrows a binding model to the closure of one of its extensions, with that extension as root. */
export function dependencyModel(model: Model, extension: string): Model | undefined {
  const byId = new Map<string, any>((model["extensions"] ?? []).map((item: any) => [item.id, item]));
  const root = byId.get(extension);
  if (root === undefined) return undefined;
  const closure = new Set<string>();
  const visit = (id: string): void => {
    const item = byId.get(id);
    if (item === undefined || closure.has(id)) return;
    closure.add(id);
    for (const dependency of item.dependencies ?? []) visit(dependency);
  };
  visit(extension);
  const vocabulary = model["vocabulary"] ?? {};
  const owned = (items: any[] | undefined): any[] =>
    (items ?? []).filter((item) => closure.has(item.owner));
  const declarations = [...(vocabulary.ownedDeclarations ?? []), ...(vocabulary.importedDeclarations ?? [])];
  return {
    ...model,
    rootExtension: { id: root.id, version: root.version, semanticSha256: root.semanticSha256 },
    extensions: model["extensions"].filter((item: any) => closure.has(item.id)),
    vocabulary: {
      ...vocabulary,
      owners: owned(vocabulary.owners),
      ownedDeclarations: declarations.filter((item) => item.owner === extension),
      importedDeclarations: declarations.filter((item) => item.owner !== extension && closure.has(item.owner)),
      interfaces: owned(vocabulary.interfaces),
      conditionFields: owned(vocabulary.conditionFields),
      interfaceFields: owned(vocabulary.interfaceFields),
      valueDomains: owned(vocabulary.valueDomains),
    },
    schemas: owned(model["schemas"]),
  };
}

/** Plans the package of a direct dependency extension, or undefined when it cannot be planned. */
export function dependencyPlan(
  plan: EmissionPlan,
  model: Model,
  extension: string,
): EmissionPlan | undefined {
  const dependency = plan.target.dependencies.find((item) => item.extension === extension);
  const narrowed = dependencyModel(model, extension);
  if (dependency === undefined || narrowed === undefined) return undefined;
  const direct: string[] =
    narrowed["extensions"].find((item: any) => item.id === extension)?.dependencies ?? [];
  const target: PackageTarget = {
    ...plan.target,
    packageKey: dependency.packageName,
    packageName: dependency.packageName,
    rootExtension: extension,
    version: dependency.version,
    // The dependency's own providers only name its imports, which examples never use.
    dependencies: direct.map((item) => ({ extension: item, packageName: `unresolved:${item}`, version: "0.0.0" })),
  };
  try {
    return buildPlan(narrowed, target);
  } catch {
    return undefined;
  }
}
